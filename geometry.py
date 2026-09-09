"""Numerical mouth-curve deformation, independent of Blender's scene state.

Binding is computed once in world space.  Posing then only samples the Bezier
curve and blends its displacements; it never changes the mesh's resting shape.
"""

import heapq

import numpy as np

FALLOFF_TYPES = frozenset(('SMOOTH', 'SPHERE', 'ROOT', 'INVERSE_SQUARE', 'SHARP', 'LINEAR', 'CONSTANT', 'RANDOM'))


def _points(values, name="points"):
    result = np.asarray(values, dtype=np.float64)
    if result.ndim != 2 or result.shape[1] != 3 or not np.isfinite(result).all():
        raise ValueError(f"{name} must be a finite N x 3 array")
    return result


def _edges(values, vertex_count):
    result = np.asarray(values)
    if result.size == 0:
        return np.empty((0, 2), dtype=np.int64)
    if (
        result.ndim != 2
        or result.shape[1] != 2
        or not np.issubdtype(result.dtype, np.integer)
    ):
        raise ValueError("Edges must contain pairs of integer vertex indices")
    result = result.astype(np.int64, copy=False)
    if result.min() < 0 or result.max() >= vertex_count:
        raise ValueError("An edge references a vertex outside the mesh")
    if np.any(result[:, 0] == result[:, 1]):
        raise ValueError("An edge cannot connect a vertex to itself")
    return result


def order_closed_loop(vertex_count, edges):
    """Return one deterministic cycle; reject open, branched or multiple loops."""
    edge_array = _edges(edges, vertex_count)
    adjacency = {}
    unique_edges = set()
    for first, second in edge_array:
        first, second = int(first), int(second)
        pair = (min(first, second), max(first, second))
        if pair in unique_edges:
            raise ValueError("The selection contains a duplicate edge")
        unique_edges.add(pair)
        adjacency.setdefault(first, []).append(second)
        adjacency.setdefault(second, []).append(first)
    if len(adjacency) < 4:
        raise ValueError("Select a closed mouth edge loop with at least four vertices")
    if any(len(neighbors) != 2 for neighbors in adjacency.values()):
        raise ValueError("Select one closed edge loop without open ends or branches")

    start = min(adjacency)
    ordered = [start]
    previous, current = start, min(adjacency[start])
    while current != start:
        ordered.append(current)
        neighbors = adjacency[current]
        following = neighbors[0] if neighbors[0] != previous else neighbors[1]
        previous, current = current, following
    if len(ordered) != len(adjacency):
        raise ValueError("Select a single connected mouth edge loop")
    return ordered


def cyclic_parameters(points):
    """Return polygon arc fractions and perimeter, including the closing edge."""
    points = _points(points)
    if len(points) < 2:
        raise ValueError("A loop needs at least two points")
    lengths = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
    total_length = float(lengths.sum())
    if not np.isfinite(total_length) or total_length <= 0.0:
        raise ValueError("The selected loop has zero usable length")
    parameters = np.concatenate(([0.0], np.cumsum(lengths[:-1]))) / total_length
    # Coincident trailing points can place a valid vertex at exactly the end
    # of the perimeter. Keep the documented half-open parameter interval.
    parameters = np.minimum(parameters, np.nextafter(1.0, 0.0))
    return parameters, total_length


def resample_loop(points, count):
    """Place controls at evenly spaced polygon arc lengths without a seam copy."""
    points = _points(points)
    if isinstance(count, bool) or int(count) != count or count < 1:
        raise ValueError("Control count must be a positive integer")
    _, total_length = cyclic_parameters(points)
    lengths = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
    cumulative = np.concatenate(([0.0], np.cumsum(lengths)))
    samples = np.arange(int(count), dtype=np.float64) * total_length / int(count)
    segments = np.searchsorted(cumulative, samples, side="right") - 1
    segments = np.minimum(segments, len(points) - 1)
    fractions = np.divide(
        samples - cumulative[segments],
        lengths[segments],
        out=np.zeros(len(samples), dtype=np.float64),
        where=lengths[segments] > 0.0,
    )
    return (
        points[segments] * (1.0 - fractions[:, None])
        + points[(segments + 1) % len(points)] * fractions[:, None]
    )


def evaluate_bezier(co, handle_left, handle_right, parameters):
    """Evaluate cyclic cubic segments at fractions of the total segment count.

    Parameters are the same fixed correspondence in the resting and posed
    curve; subtracting the two evaluations keeps the original mesh untouched
    even when a small number of controls only approximates its edge loop.
    """
    co = _points(co, "Control points")
    handle_left = _points(handle_left, "Left handles")
    handle_right = _points(handle_right, "Right handles")
    if not len(co) or co.shape != handle_left.shape or co.shape != handle_right.shape:
        raise ValueError("Control points and handles must have equal, nonzero lengths")
    parameters = np.asarray(parameters, dtype=np.float64)
    if parameters.ndim != 1 or not np.isfinite(parameters).all():
        raise ValueError("Curve parameters must be a finite one-dimensional array")
    position = np.remainder(parameters, 1.0) * len(co)
    integral = np.floor(position).astype(np.int64)
    # A negative parameter less than machine epsilon can round its remainder
    # to exactly 1.0; wrapping the segment also covers that seam case.
    segment = integral % len(co)
    t = (position - integral)[:, None]
    inverse = 1.0 - t
    following = (segment + 1) % len(co)
    return (
        inverse**3 * co[segment]
        + 3.0 * inverse**2 * t * handle_right[segment]
        + 3.0 * inverse * t**2 * handle_left[following]
        + t**3 * co[following]
    )


def bezier_tangents(co, handle_left, handle_right, parameters):
    """Unit tangents at fixed cyclic parameters; degenerate segments return zero."""
    co = _points(co)
    left, right = _points(handle_left), _points(handle_right)
    if co.shape != left.shape or co.shape != right.shape or not len(co):
        raise ValueError("Control points and handles must have matching lengths")
    position = np.remainder(parameters, 1.0) * len(co)
    segment = np.floor(position).astype(np.int64) % len(co)
    t = (position - np.floor(position))[:, None]
    following = (segment + 1) % len(co)
    tangent = ((1-t)**2 * (right[segment] - co[segment])
               + 2*(1-t)*t * (left[following] - right[segment])
               + t**2 * (co[following] - left[following]))
    lengths = np.linalg.norm(tangent, axis=1)
    chord = co[following] - co[segment]
    degenerate = lengths <= np.linalg.norm(chord, axis=1) * 1e-12
    tangent[degenerate] = chord[degenerate]
    lengths = np.linalg.norm(tangent, axis=1)
    return np.divide(tangent, lengths[:, None], out=np.zeros_like(tangent), where=lengths[:, None] > 0)


def interpolate_tilt(tilts, parameters, interpolation='LINEAR'):
    """Sample native cyclic tilt values in radians, without angle wrapping.

    Blender's Cardinal mode uses tension 0.71; see key_curve_position_weights:
    https://github.com/blender/blender/blob/main/source/blender/blenkernel/intern/key.cc
    """
    tilts = np.asarray(tilts, dtype=np.float64)
    parameters = np.asarray(parameters, dtype=np.float64)
    if (tilts.ndim != 1 or not len(tilts) or not np.isfinite(tilts).all()
            or parameters.ndim != 1 or not np.isfinite(parameters).all()):
        raise ValueError("Tilt values and parameters must be finite arrays")
    position = np.remainder(parameters, 1.0) * len(tilts)
    segment = np.floor(position).astype(np.int64) % len(tilts)
    t = position - np.floor(position)
    a, b = tilts[segment], tilts[(segment + 1) % len(tilts)]
    if interpolation == 'LINEAR':
        return a * (1-t) + b * t
    if interpolation == 'EASE':
        blend = t*t*(3-2*t)
        return a * (1-blend) + b * blend
    previous, following = tilts[(segment-1) % len(tilts)], tilts[(segment+2) % len(tilts)]
    if interpolation == 'CARDINAL':
        # Cubic Hermite basis with Blender's cardinal endpoint tangents.
        return ((2*t**3-3*t*t+1)*a + (-2*t**3+3*t*t)*b
                + (t**3-2*t*t+t)*.71*(b-previous)
                + (t**3-t*t)*.71*(following-a))
    if interpolation == 'BSPLINE':
        return (((1-t)**3)*previous + (3*t**3-6*t*t+4)*a
                + (-3*t**3+3*t*t+3*t+1)*b + t**3*following) / 6
    raise ValueError("Unsupported curve tilt interpolation")


def corner_layout(points, count, corners):
    """Sample two lip arcs, pinning controls to explicit corner vertex offsets.

    Return control coordinates and a curve parameter for every original loop
    vertex. Works in any orientation; no world-up or automatic left/right guess.
    """
    points = _points(points)
    if int(count) != count or not 4 <= count <= 64:
        raise ValueError("Choose between 4 and 64 controls")
    count = int(count)
    if len(corners) != 2 or len(set(corners)) != 2 or any(i < 0 or i >= len(points) for i in corners):
        raise ValueError("Choose two distinct corner vertices on the stored mouth loop")
    a, b = map(int, corners)
    split = (b - a) % len(points)
    if split < 2 or len(points) - split < 2:
        raise ValueError("Choose opposite mouth corners, with loop vertices between them on both sides")
    order = (a + np.arange(len(points) + 1)) % len(points)
    arcs = (order[:split + 1], order[split:])
    distances = [np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(points[arc], axis=0), axis=1))] for arc in arcs]
    lengths = [distance[-1] for distance in distances]
    if min(lengths) <= 0:
        raise ValueError("Both lip arcs must have nonzero length")
    first_count = int(np.clip(round(count * lengths[0] / sum(lengths)), 2, count - 2))
    counts = (first_count, count - first_count)
    controls = []
    parameters = np.zeros(len(points), dtype=np.float64)
    offset = 0
    for arc, distance, segments in zip(arcs, distances, counts):
        samples = np.arange(segments) * distance[-1] / segments
        controls.append(np.column_stack([np.interp(samples, distance, points[arc, axis]) for axis in range(3)]))
        parameters[arc[:-1]] = (offset + segments * distance[:-1] / distance[-1]) / count
        offset += segments
    return np.concatenate(controls), parameters


def symmetrize_bezier(rest, posed, direction, tilts=None):
    """Mirror a cyclic control layout across mesh-local X=0, reversing handles.

    Pair using the neutral layout, not the pose (controls may cross the plane).
    A cyclic reflection preserves lip order and gives a one-to-one mapping,
    including odd counts and centerline controls. If tilts are supplied, return
    (coordinates, mirrored_tilts); otherwise return coordinates only.
    """
    rest = np.asarray(rest, dtype=np.float64)
    posed = np.asarray(posed, dtype=np.float64)
    if (rest.ndim != 3 or rest.shape[1:] != (3, 3) or rest.shape != posed.shape
            or len(rest) < 4 or not np.isfinite(rest).all() or not np.isfinite(posed).all()):
        raise ValueError("Symmetry requires matching finite Bézier control layouts")
    if direction not in ('POSITIVE', 'NEGATIVE'):
        raise ValueError("Choose +X to -X or -X to +X")
    mirror = np.array([-1.0, 1.0, 1.0])
    co = rest[:, 0]
    width = np.ptp(co[:, 0])
    epsilon = max(width * 1e-5, 1e-8)
    if co[:, 0].min() >= -epsilon or co[:, 0].max() <= epsilon:
        raise ValueError("The mouth must straddle the mesh's local X=0 plane to symmetrize")
    indices = np.arange(len(co))
    mappings = [(offset - indices) % len(co) for offset in indices]
    mapping = min(mappings, key=lambda m: np.sum((co[m] - co * mirror) ** 2))
    result = posed.copy()
    result_tilts = None if tilts is None else np.asarray(tilts, dtype=np.float64).copy()
    if result_tilts is not None and (result_tilts.shape != (len(co),) or not np.isfinite(result_tilts).all()):
        raise ValueError("Tilt values must match the control count and be finite")
    source_sign = 1 if direction == 'POSITIVE' else -1
    for i, j in enumerate(mapping):
        if i > j:
            continue
        if i == j:
            # Self-paired controls are the seam between the two sides.
            result[i, 0, 0] = 0
            handle = 1 if source_sign * rest[i, 1, 0] > source_sign * rest[i, 2, 0] else 2
            other = 3 - handle
            result[i, other] = result[i, handle] * mirror
        else:
            if co[i, 0] * co[j, 0] > epsilon ** 2:
                raise ValueError("Controls cannot be paired across local X. Refine the mouth corners and rebuild controls first")
            source, target = (i, j) if source_sign * co[i, 0] > source_sign * co[j, 0] else (j, i)
            result[target] = posed[source, [0, 2, 1]] * mirror
            if result_tilts is not None:
                # Reflection and reversed spline direction cancel: same tilt sign.
                result_tilts[target] = result_tilts[source]
    return result if result_tilts is None else (result, result_tilts)


def _radius(value):
    value = float(value)
    if not np.isfinite(value) or value < 0.0:
        raise ValueError("Influence distance must be finite and nonnegative")
    return value


def build_binding(vertices, edges, loop_indices, radius):
    """Bind nearby connected surface vertices to their four closest loop seeds.

    Distances follow mesh edges in world space, so separate teeth or another
    nearby, disconnected surface never acquire influence.  A bounded Dijkstra
    search retains at most four source labels per mesh vertex.  Three loop
    edges of extra search distance ensure all affected vertices can find four
    sources even when they are close to the influence boundary.

    Arrays returned are ordered by mesh vertex index.  ``sources`` indexes the
    ordered loop, not the mesh.  ``loop_mask`` preserves exact loop motion even
    for zero-length mesh edges and a zero influence radius.
    """
    vertices = _points(vertices, "Vertices")
    edges = _edges(edges, len(vertices))
    radius = _radius(radius)
    loop_indices = np.asarray(loop_indices)
    if (
        loop_indices.ndim != 1
        or not np.issubdtype(loop_indices.dtype, np.integer)
        or len(loop_indices) < 4
        or len(np.unique(loop_indices)) != len(loop_indices)
        or loop_indices.min() < 0
        or loop_indices.max() >= len(vertices)
    ):
        raise ValueError("The loop must contain at least four unique mesh vertex indices")
    loop_indices = loop_indices.astype(np.int64, copy=False)
    adjacency = [[] for _ in vertices]
    edge_lengths = np.linalg.norm(vertices[edges[:, 0]] - vertices[edges[:, 1]], axis=1)
    for (first, second), distance in zip(edges, edge_lengths):
        adjacency[first].append((int(second), float(distance)))
        adjacency[second].append((int(first), float(distance)))
    for first, second in zip(loop_indices, np.roll(loop_indices, -1)):
        if not any(neighbor == second for neighbor, _ in adjacency[first]):
            raise ValueError("Consecutive mouth loop vertices must share a mesh edge")
    loop_lengths = np.linalg.norm(
        vertices[loop_indices] - vertices[np.roll(loop_indices, -1)], axis=1
    )
    if not np.any(loop_lengths > 0.0):
        raise ValueError("The selected loop has zero usable length")
    search_limit = radius + 3.0 * float(loop_lengths.max())
    nearest = [{} for _ in vertices]
    heap = [(0.0, source, int(vertex)) for source, vertex in enumerate(loop_indices)]
    heapq.heapify(heap)
    while heap:
        distance, source, vertex = heapq.heappop(heap)
        labels = nearest[vertex]
        if source in labels or len(labels) == 4:
            continue
        labels[source] = distance
        for neighbor, length in adjacency[vertex]:
            candidate = distance + length
            known = nearest[neighbor]
            if candidate <= search_limit and source not in known and len(known) < 4:
                heapq.heappush(heap, (candidate, source, neighbor))

    loop_sources = {int(vertex): source for source, vertex in enumerate(loop_indices)}
    indices = np.array(
        [
            vertex
            for vertex, labels in enumerate(nearest)
            if vertex in loop_sources or (labels and radius > 0.0 and min(labels.values()) < radius)
        ],
        dtype=np.int64,
    )
    sources = np.zeros((len(indices), 4), dtype=np.int64)
    weights = np.zeros((len(indices), 4), dtype=np.float64)
    distances = np.zeros(len(indices), dtype=np.float64)
    loop_mask = np.zeros(len(indices), dtype=bool)
    for row, vertex in enumerate(indices):
        if vertex in loop_sources:
            sources[row, 0] = loop_sources[vertex]
            weights[row, 0] = 1.0
            loop_mask[row] = True
            continue
        labels = sorted(nearest[vertex].items(), key=lambda item: (item[1], item[0]))
        source_ids, lengths = zip(*labels)
        lengths = np.asarray(lengths)
        distances[row] = lengths[0]
        sources[row, : len(labels)] = source_ids
        zero = lengths == 0.0
        if zero.any():
            blend = zero.astype(np.float64)
        elif len(labels) == 1 or lengths[-1] == lengths[0]:
            blend = np.ones(len(labels), dtype=np.float64)
        else:
            # Modified Shepard weights vanish at the farthest source, avoiding
            # a jump when the fourth-nearest source changes across the surface.
            # Normalize distances first to avoid overflow on tiny-scale meshes.
            blend = ((1.0 - lengths / lengths[-1]) * (lengths[0] / lengths)) ** 2
        weights[row, : len(labels)] = blend / blend.sum()
    return {
        "indices": indices,
        "sources": sources,
        "weights": weights,
        "distances": distances,
        "loop_mask": loop_mask,
    }


def binding_falloff(binding, radius, falloff=1.0, falloff_type='SMOOTH', random_seed=0):
    """Shared distance profiles; strength 1 matches Blender's profile shapes.

    Random uses a fixed vertex-ID hash rather than a per-update RNG. Profile reference:
    https://github.com/blender/blender/blob/main/source/blender/editors/transform/transform_generics.cc
    """
    radius = _radius(radius)
    falloff = float(falloff)
    if not np.isfinite(falloff) or falloff <= 0.0:
        raise ValueError("Falloff must be finite and greater than zero")
    if falloff_type not in FALLOFF_TYPES:
        raise ValueError('Choose a supported falloff type')
    distances = np.asarray(binding["distances"], dtype=np.float64)
    loop_mask = np.asarray(binding.get("loop_mask", distances == 0.0), dtype=bool)
    if radius == 0.0:
        fade = loop_mask.astype(np.float64)
    else:
        normalized = np.clip(distances / radius, 0.0, 1.0)
        remaining = 1.0 - normalized
        if falloff_type == 'SMOOTH':
            fade = remaining**2 * (1.0 + 2.0 * normalized)
        elif falloff_type == 'SPHERE':
            fade = np.sqrt(np.maximum(0.0, 1.0-normalized**2))
        elif falloff_type == 'ROOT':
            fade = np.sqrt(remaining)
        elif falloff_type == 'INVERSE_SQUARE':
            fade = 1.0-normalized**2
        elif falloff_type == 'SHARP':
            fade = remaining**2
        elif falloff_type == 'LINEAR':
            fade = remaining
        elif falloff_type == 'CONSTANT':
            fade = (distances < radius).astype(np.float64)
        else:
            # Integer avalanche hash is independent of row order, radius and reload.
            seed = int(random_seed) & 0xffffffff
            hashed = (np.asarray(binding['indices'], dtype=np.uint64) + 1 + seed * 2654435761) & 0xffffffff
            hashed = ((hashed ^ (hashed >> 16)) * 0x7feb352d) & 0xffffffff
            hashed = ((hashed ^ (hashed >> 15)) * 0x846ca68b) & 0xffffffff
            hashed ^= hashed >> 16
            fade = remaining * (hashed.astype(np.float64) / 4294967296.0)
        fade = np.clip(fade, 0, 1) ** falloff
        fade[loop_mask] = 1.0
    return fade


def apply_binding(binding, loop_displacements, radius, falloff=1.0, falloff_type='SMOOTH', random_seed=0):
    """Blend loop translations with a smooth distance fade."""
    loop_displacements = _points(loop_displacements, "Loop displacements")
    fade = binding_falloff(binding, radius, falloff, falloff_type, random_seed)
    blended = np.einsum(
        "ij,ijk->ik", binding["weights"], loop_displacements[binding["sources"]]
    )
    return blended * fade[:, None]


def apply_tilt(binding, offsets, tangents, angles, radius, falloff=1.0, falloff_type='SMOOTH', random_seed=0):
    """Rotate neighbor offsets about loop tangents using Rodrigues' formula.

    Offsets and tangents share mesh-local coordinates. Blend only the twist
    displacement, so zero tilt is exactly the pre-existing translation behavior.
    Stored-loop vertices are the pivots, not the approximating Bézier curve.
    """
    axes = tangents[binding['sources']]
    theta = angles[binding['sources']]
    theta = np.where(np.linalg.norm(axes, axis=2) > 0, theta, 0)
    cosine = np.cos(theta)[..., None]
    sine = np.sin(theta)[..., None]
    twist = ((cosine-1)*offsets + sine*np.cross(axes, offsets)
             + (1-cosine)*axes*np.sum(axes*offsets, axis=2, keepdims=True))
    blended = np.einsum('ij,ijk->ik', binding['weights'], twist)
    # Keep the selected loop exactly on the twist axis, including coarse layouts.
    blended[binding['loop_mask']] = 0
    return blended * binding_falloff(binding, radius, falloff, falloff_type, random_seed)[:, None]
