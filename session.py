"""Blender lifecycle for a Bézier displacement field and temporary shape key.

Persistent links are ID pointers; disposable numeric caches contain no Blender RNA.
Only the owned preview key is written. Existing keys, modifiers and weights stay intact.
"""

import hashlib
import uuid

import bmesh
import bpy
import numpy as np
from bpy.app.handlers import persistent
from mathutils import Matrix

from . import geometry

_STATE = "_curvemorph_state"
_OWNER = "_curvemorph_token"
_CACHE = {}
_BUSY = False
_HANDLE_TYPES = ('FREE', 'AUTO', 'VECTOR', 'ALIGNED', 'AUTO_CLAMPED')


def _coords(collection):
    result = np.empty(len(collection) * 3, dtype=np.float64)
    collection.foreach_get("co", result)
    return result.reshape((-1, 3))


def _edges(mesh):
    result = np.empty(len(mesh.edges) * 2, dtype=np.int32)
    mesh.edges.foreach_get("vertices", result)
    return result.reshape((-1, 2))


def _digest(array):
    return hashlib.blake2b(np.ascontiguousarray(array).tobytes(), digest_size=16).hexdigest()


def _topology(mesh):
    return f"{len(mesh.vertices)}:{len(mesh.polygons)}:{_digest(_edges(mesh))}"


def _transform(points, matrix):
    matrix = np.asarray(matrix, dtype=np.float64)
    return points @ matrix[:3, :3].T + matrix[:3, 3]


def _basis(mesh_obj):
    keys = mesh_obj.data.shape_keys
    return _coords(keys.reference_key.data if keys else mesh_obj.data.vertices)


def _curve_arrays(curve):
    if len(curve.data.splines) != 1:
        raise ValueError("Use one closed Bézier spline. Undo the added or removed spline.")
    spline = curve.data.splines[0]
    if spline.type != 'BEZIER' or not spline.use_cyclic_u:
        raise ValueError("The control curve must stay a closed Bézier spline.")
    return np.array([
        (tuple(point.co), tuple(point.handle_left), tuple(point.handle_right))
        for point in spline.bezier_points
    ], dtype=np.float64)


def _curve_tilts(curve):
    return np.array([point.tilt for point in curve.data.splines[0].bezier_points], dtype=np.float64)


def _handle_types(curve):
    return [_HANDLE_TYPES.index(kind) for point in curve.data.splines[0].bezier_points
            for kind in (point.handle_left_type, point.handle_right_type)]


def _restore_curve(curve, arrays, types, tilts):
    # Write all coordinates before restoring automatic/aligned constraints.
    interpolation = curve.data.splines[0].tilt_interpolation
    _set_curve(curve, arrays[:, 0], {i: values[1:] for i, values in enumerate(arrays)})
    curve.data.splines[0].tilt_interpolation = interpolation
    for i, point in enumerate(curve.data.splines[0].bezier_points):
        point.handle_left_type = _HANDLE_TYPES[int(types[2*i])]
        point.handle_right_type = _HANDLE_TYPES[int(types[2*i+1])]
        point.tilt = float(tilts[i])
    curve.data.update_tag()
    bpy.context.view_layer.update()


def _check_not_fitting(mesh_obj):
    if mesh_obj.curvemorph.fit_editing:
        raise ValueError('Apply Fit or Cancel before changing the pose or setup.')


def _set_curve(curve, coordinates, corner_handles=None):
    curve.data.splines.clear()
    spline = curve.data.splines.new('BEZIER')
    spline.bezier_points.add(len(coordinates) - 1)
    spline.use_cyclic_u = True
    for point, co in zip(spline.bezier_points, coordinates):
        point.co = co
        point.tilt = 0.0
        point.handle_left_type = 'AUTO'
        point.handle_right_type = 'AUTO'
        point.select_control_point = False
        point.select_left_handle = False
        point.select_right_handle = False
    for index, handles in (corner_handles or {}).items():
        point = spline.bezier_points[index]
        point.handle_left_type = point.handle_right_type = 'FREE'
        point.handle_left, point.handle_right = handles
    curve.data.update_tag()
    bpy.context.view_layer.update()


def resolve_target(context):
    obj = context.active_object
    if obj is not None:
        if obj.type == 'MESH' and obj.curvemorph.token:
            return obj
        if obj.curvemorph_target is not None:
            target = obj.curvemorph_target
            if target.type == 'MESH' and target.curvemorph.curve == obj and target.curvemorph.token:
                return target
    target = context.scene.curvemorph_settings.target
    return target if target and target.type == 'MESH' and target.curvemorph.token else None


def _owned_curve(mesh_obj):
    state = mesh_obj.curvemorph
    curve = state.curve
    if (curve is None or curve.type != 'CURVE' or curve.curvemorph_target != mesh_obj
            or curve.get(_OWNER) != state.token):
        raise ValueError("Controls are missing or belong to another mesh. Finish this session and create controls again.")
    return curve


def _preview(mesh_obj):
    state = mesh_obj.curvemorph
    keys = mesh_obj.data.shape_keys
    expected = "__CurveMorph_" + state.token
    key = keys.key_blocks.get(state.preview_name) if keys else None
    if key is None or state.preview_name != expected:
        raise ValueError("The temporary preview shape key was removed or renamed. Finish this session and start again.")
    return key


def _set_error(mesh_obj, message):
    if mesh_obj.curvemorph.error != message:
        mesh_obj.curvemorph.error = message


def _object_mode():
    if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')


def _activate(context, obj):
    if obj.name not in context.view_layer.objects:
        raise ValueError("The target is not in the current view layer.")
    _object_mode()
    for selected in list(context.selected_objects):
        selected.select_set(False)
    obj.hide_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


def _check_editable(mesh_obj):
    if mesh_obj.library or mesh_obj.data.library or mesh_obj.override_library:
        raise ValueError("Use a local editable copy of the mesh.")
    if mesh_obj.data.users != 1:
        raise ValueError("Make this mesh single-user before posing (Object > Relations > Make Single User > Object Data).")
    determinant = mesh_obj.matrix_world.determinant()
    if determinant == 0 or not np.isfinite(determinant):
        raise ValueError("The mesh has a zero scale. Restore a nonzero scale before creating controls.")


def _check_neutral_keys(mesh_obj, preview_name=""):
    from . import facial
    owned_previews = facial.preview_names(mesh_obj)
    keys = mesh_obj.data.shape_keys
    if keys:
        if not keys.use_relative:
            raise ValueError("Absolute shape keys are not supported. Use a mesh with relative shape keys.")
        if any(key.name != preview_name and key.name not in owned_previews and abs(key.value) > 1e-6 and not key.mute
               for key in keys.key_blocks[1:]):
            raise ValueError("Set other shape key values to zero while authoring a mouth pose.")
    if mesh_obj.show_only_shape_key:
        raise ValueError("Turn off Show Only Shape Key while authoring a mouth pose.")


def _layout(mesh_obj, loop, count, corners=()):
    world = _transform(_basis(mesh_obj), mesh_obj.matrix_world)[loop]
    if corners:
        if any(index not in loop for index in corners):
            raise ValueError("Both corners must be on the mouth loop.")
        controls, parameters = geometry.corner_layout(world, count, [loop.index(i) for i in corners])
    else:
        controls = geometry.resample_loop(world, count)
        parameters, _ = geometry.cyclic_parameters(world)
    return _transform(controls, mesh_obj.matrix_world.inverted()), parameters


def _group_weights(mesh_obj, indices, group_name, missing_message):
    if not group_name:
        return np.ones(len(indices), dtype=np.float64)
    group = mesh_obj.vertex_groups.get(group_name)
    if group is None:
        raise ValueError(missing_message)
    return np.array([next((a.weight for a in mesh_obj.data.vertices[int(i)].groups if a.group == group.index), 0.0)
                     for i in indices], dtype=np.float64)


def _mask_weights(mesh_obj, indices):
    state = mesh_obj.curvemorph
    message = 'The influence group is missing. Choose a group in Setup or turn off Limit to Vertex Group.'
    if state.use_mask and not state.mask_group:
        raise ValueError(message)
    return _group_weights(mesh_obj, indices, state.mask_group if state.use_mask else '', message)


def _twist_mask_weights(mesh_obj, indices):
    return _group_weights(mesh_obj, indices, mesh_obj.curvemorph.twist_mask_group,
                         'The twist mask group is missing. Choose an existing group or clear the Twist Mask field.')


def create_session(context, control_count=8, radius=0.0, falloff=1.0, corner_vertices=(),
                   falloff_type='SMOOTH', random_seed=0):
    mesh_obj = context.active_object
    if mesh_obj is None or mesh_obj.type != 'MESH' or mesh_obj.mode != 'EDIT':
        raise ValueError("Select a closed mouth edge loop in mesh Edit Mode first.")
    _check_editable(mesh_obj)
    if mesh_obj.curvemorph.token:
        raise ValueError("This mesh already has controls. Use Edit Controls or Finish first.")
    if not 4 <= control_count <= 64:
        raise ValueError("Choose between 4 and 64 controls.")
    if not np.isfinite(radius) or radius < 0 or not 0.2 <= falloff <= 4.0:
        raise ValueError("Choose a nonnegative influence distance and a falloff between 0.2 and 4.")
    if falloff_type not in geometry.FALLOFF_TYPES:
        raise ValueError('Choose a supported falloff type')
    keys = mesh_obj.data.shape_keys
    _check_neutral_keys(mesh_obj)
    bm = bmesh.from_edit_mesh(mesh_obj.data)
    bm.verts.index_update()
    selected = [(edge.verts[0].index, edge.verts[1].index) for edge in bm.edges if edge.select]
    loop = geometry.order_closed_loop(len(bm.verts), selected)
    if {vertex.index for vertex in bm.verts if vertex.select} != set(loop):
        raise ValueError("Select only one closed edge loop, without extra selected vertices.")
    local = np.array([tuple(vertex.co) for vertex in bm.verts], dtype=np.float64)
    edges = np.array([(e.verts[0].index, e.verts[1].index) for e in bm.edges], dtype=np.int32)
    world = _transform(local, mesh_obj.matrix_world)
    parameters, _length = geometry.cyclic_parameters(world[loop])
    if radius == 0:
        radius = max(float(np.linalg.norm(np.ptp(world[loop], axis=0))) * 0.25, 1e-6)
    binding = geometry.build_binding(world, edges, loop, radius)
    _mask_weights(mesh_obj, binding["indices"])
    _twist_mask_weights(mesh_obj, binding['indices'])
    coordinates = _transform(geometry.resample_loop(world[loop], control_count), mesh_obj.matrix_world.inverted())
    if corner_vertices:
        if any(i not in loop for i in corner_vertices):
            raise ValueError("Both corners must be on the mouth loop.")
        controls, parameters = geometry.corner_layout(world[loop], control_count, [loop.index(i) for i in corner_vertices])
        coordinates = _transform(controls, mesh_obj.matrix_world.inverted())
    _object_mode()
    if not np.allclose(_basis(mesh_obj), local, rtol=1e-6, atol=1e-7):
        bpy.ops.object.mode_set(mode='EDIT')
        raise ValueError("Select the Basis shape key before creating mouth controls.")
    token = uuid.uuid4().hex
    curve = curve_data = preview = None
    created_basis = keys is None
    original_active = mesh_obj.active_shape_key_index
    try:
        if created_basis:
            mesh_obj.shape_key_add(name="Basis", from_mix=False)
        preview = mesh_obj.shape_key_add(name="__CurveMorph_" + token, from_mix=False)
        preview.value = 1.0
        preview.relative_key = mesh_obj.data.shape_keys.reference_key
        curve_data = bpy.data.curves.new("Mouth Controls", 'CURVE')
        curve_data.dimensions = '3D'
        curve_data.resolution_u = 16
        curve = bpy.data.objects.new("Mouth Controls · " + mesh_obj.name, curve_data)
        context.scene.collection.objects.link(curve)
        curve.parent = mesh_obj
        curve.matrix_parent_inverse = Matrix.Identity(4)
        curve.matrix_basis = Matrix.Identity(4)
        curve.show_in_front = True
        curve.hide_render = True
        curve[_OWNER] = token
        curve.curvemorph_target = mesh_obj
        handles = geometry.corner_handles(local[loop], coordinates, parameters,
                                          [loop.index(i) for i in corner_vertices])
        _set_curve(curve, coordinates, handles)
        rest = _curve_arrays(curve)
        samples = geometry.evaluate_bezier(rest[:, 0], rest[:, 1], rest[:, 2], parameters)
        mesh_obj[_STATE] = {
            "version": 1, "loop": list(map(int, loop)), "parameters": parameters.tolist(),
            "rest_curve": rest.ravel().tolist(), "rest_samples": samples.ravel().tolist(),
            "topology": _topology(mesh_obj.data), "basis_hash": _digest(_basis(mesh_obj)),
            "created_basis": created_basis, "original_active": original_active,
            "corners": list(corner_vertices),
            "corner_controls": list(handles),
        }
        state = mesh_obj.curvemorph
        state.curve = curve
        state.preview_name = preview.name
        state.radius = radius
        state.falloff = falloff
        state.falloff_type = falloff_type
        state.random_seed = random_seed
        state.control_count = control_count
        state.error = ""
        state.last_saved_name = ""
        state.setup_editing = False
        state.fit_editing = False
        state.token = token
        context.scene.curvemorph_settings.target = mesh_obj
        _CACHE.pop(token, None)
        _activate(context, curve)
        update_session(mesh_obj, force=True, prepared_binding=binding)
        from . import setup
        mesh_obj[setup.KEY] = {"loop": loop, "corners": list(corner_vertices), "topology": _topology(mesh_obj.data)}
        return mesh_obj
    except Exception:
        mesh_obj.curvemorph.token = ""
        mesh_obj.curvemorph.curve = None
        if _STATE in mesh_obj:
            del mesh_obj[_STATE]
        if curve is not None:
            bpy.data.objects.remove(curve, do_unlink=True)
        if curve_data is not None and curve_data.users == 0:
            bpy.data.curves.remove(curve_data)
        if preview is not None:
            mesh_obj.shape_key_remove(preview)
        if created_basis and mesh_obj.data.shape_keys and len(mesh_obj.data.shape_keys.key_blocks) == 1:
            mesh_obj.shape_key_remove(mesh_obj.data.shape_keys.reference_key)
        _activate(context, mesh_obj)
        bpy.ops.object.mode_set(mode='EDIT')
        raise


def update_session(mesh_obj, force=False, prepared_binding=None):
    """Read live curve Edit Mode data without changing selection or mode."""
    state = mesh_obj.curvemorph
    if not state.token:
        return
    if state.setup_editing or state.fit_editing:
        _set_error(mesh_obj, "")
        return
    _check_editable(mesh_obj)
    if mesh_obj.mode == 'EDIT':
        raise ValueError("Edit the mouth controls, not the source mesh. Finish before changing mesh topology.")
    raw = mesh_obj.get(_STATE)
    if raw is None:
        raise ValueError("Session metadata is missing. Finish this session and create controls again.")
    curve = _owned_curve(mesh_obj)
    preview = _preview(mesh_obj)
    _check_neutral_keys(mesh_obj, preview.name)
    if len(mesh_obj.data.vertices) != len(preview.data):
        raise ValueError("Mesh topology changed. Undo that change or finish this session.")
    arrays = _curve_arrays(curve)
    tilts = _curve_tilts(curve)
    interpolation = curve.data.splines[0].tilt_interpolation
    if not np.isfinite(tilts).all():
        raise ValueError("The controls contain non-finite tilt. Clear tilt with Alt+T.")
    if len(arrays) != state.control_count:
        raise ValueError("Control count changed outside Fit. Undo it, then use Adjust Neutral Fit to add or remove points.")
    tilts -= np.asarray(raw.get('rest_tilts', [0.0] * state.control_count))
    if curve.parent == mesh_obj and curve.parent_type == 'OBJECT' and not curve.constraints:
        relative = curve.matrix_parent_inverse @ curve.matrix_basis
    else:
        relative = mesh_obj.matrix_world.inverted() @ curve.matrix_world
    linear = np.asarray(mesh_obj.matrix_world, dtype=np.float64)[:3, :3].copy()
    signature = (arrays.tobytes(), tilts.tobytes(), interpolation, np.asarray(relative).tobytes(),
                 state.radius, state.falloff, state.falloff_type, state.random_seed, linear.tobytes())
    cache = _CACHE.get(state.token)
    mask = _mask_weights(mesh_obj, cache["binding"]["indices"]) if cache else None
    twist_mask = _twist_mask_weights(mesh_obj, cache['binding']['indices']) if cache else None
    full_signature = signature + ((mask.tobytes() if mask is not None else None), state.use_mask, state.mask_group,
                                 (twist_mask.tobytes() if twist_mask is not None else None), state.twist_mask_group)
    if cache and cache.get("signature") == full_signature and not force:
        _set_error(mesh_obj, "")
        return
    if cache is None or force or cache.get("metric") != linear.tobytes() or cache.get("radius") != state.radius:
        if _topology(mesh_obj.data) != raw["topology"]:
            raise ValueError("Mesh topology changed. Undo that change or finish this session.")
        basis = _basis(mesh_obj)
        if _digest(basis) != raw["basis_hash"]:
            raise ValueError("The Basis was edited. Undo that edit or finish and recreate the controls.")
        binding = prepared_binding or geometry.build_binding(
            _transform(basis, mesh_obj.matrix_world), _edges(mesh_obj.data), list(raw["loop"]), state.radius,
        )
        cache = {"basis": basis, "binding": binding, "radius": state.radius, "metric": linear.tobytes()}
        _CACHE[state.token] = cache
        mask = _mask_weights(mesh_obj, binding["indices"])
        twist_mask = _twist_mask_weights(mesh_obj, binding['indices'])
    coordinates = _transform(arrays.reshape((-1, 3)), relative).reshape(arrays.shape)
    parameters = np.asarray(raw["parameters"], dtype=np.float64)
    rest = np.asarray(raw["rest_samples"], dtype=np.float64).reshape((-1, 3))
    current = geometry.evaluate_bezier(coordinates[:, 0], coordinates[:, 1], coordinates[:, 2], parameters)
    delta = current - rest
    result = cache["basis"].copy()
    binding = cache["binding"]
    displacement = geometry.apply_binding(binding, delta, state.radius, state.falloff,
                                           state.falloff_type, state.random_seed)
    if np.any(tilts):
        if 'tilt_offsets' not in cache:
            loop_basis = cache['basis'][list(raw['loop'])]
            cache['tilt_offsets'] = cache['basis'][binding['indices'], None, :] - loop_basis[binding['sources']]
        tangents = geometry.bezier_tangents(coordinates[:, 0], coordinates[:, 1], coordinates[:, 2],
                                            parameters, average_joints=True)
        angles = geometry.interpolate_tilt(tilts, parameters, interpolation)
        displacement += geometry.apply_tilt(binding, cache['tilt_offsets'], tangents, angles,
                                            state.radius, state.falloff, state.falloff_type,
                                            state.random_seed) * twist_mask[:, None]
    result[binding["indices"]] += displacement * mask[:, None]
    if not np.isfinite(result).all():
        raise ValueError("The controls produced non-finite positions. Undo the last control edit.")
    cache["signature"] = signature + (mask.tobytes(), state.use_mask, state.mask_group,
                                       twist_mask.tobytes(), state.twist_mask_group)
    preview.data.foreach_set("co", result.ravel())
    preview.value = 1.0
    preview.mute = False
    mesh_obj.data.update()
    _set_error(mesh_obj, "")


def edit_controls(context, mesh_obj):
    curve = _owned_curve(mesh_obj)
    if mesh_obj.curvemorph.setup_editing:
        _object_mode()
        mesh_obj.curvemorph.setup_editing = False
        try:
            update_session(mesh_obj, force=True)
        except Exception:
            mesh_obj.curvemorph.setup_editing = True
            _preview(mesh_obj).value = 0
            raise
    _activate(context, curve)
    bpy.ops.object.mode_set(mode='EDIT')


def _check_fit_curve(mesh_obj, curve):
    if curve.parent != mesh_obj or curve.parent_type != 'OBJECT' or curve.constraints:
        raise ValueError('Keep the control curve parented to its mesh without constraints while adjusting its fit.')


def _clear_fit_metadata(mesh_obj):
    raw = mesh_obj[_STATE]
    for key in ('fit_matrix', 'fit_parent_inverse'):
        if key in raw:
            del raw[key]
    mesh_obj.curvemorph.fit_editing = False
    _CACHE.pop(mesh_obj.curvemorph.token, None)


def begin_curve_fit(context, mesh_obj):
    """Pause deformation at neutral; retain a persistent, undoable Cancel copy."""
    _check_not_fitting(mesh_obj)
    if mesh_obj.curvemorph.setup_editing:
        raise ValueError('Resume Current Controls or Apply Setup before adjusting the curve fit.')
    curve = _owned_curve(mesh_obj)
    _check_fit_curve(mesh_obj, curve)
    _object_mode()
    update_session(mesh_obj, force=True)
    if np.max(np.abs(_coords(_preview(mesh_obj).data) - _basis(mesh_obj))) > 1e-8:
        raise ValueError('Save any pose you want to keep, then Reset Pose before adjusting the curve fit.')
    state, raw = mesh_obj.curvemorph, mesh_obj[_STATE]
    backup = curve.data.copy()
    backup.name = 'Mouth Fit Backup'
    state.fit_backup = backup
    raw['fit_matrix'] = np.asarray(curve.matrix_basis).ravel().tolist()
    raw['fit_parent_inverse'] = np.asarray(curve.matrix_parent_inverse).ravel().tolist()
    state.fit_editing = True
    try:
        edit_controls(context, mesh_obj)
    except Exception:
        state.fit_backup = None
        _clear_fit_metadata(mesh_obj)
        if backup.users == 0:
            bpy.data.curves.remove(backup)
        raise


def apply_curve_fit(mesh_obj):
    """Adopt edited coordinates, handles and tilt as the neutral control curve."""
    state = mesh_obj.curvemorph
    if not state.fit_editing or state.fit_backup is None:
        raise ValueError('Click Adjust Curve Fit first.')
    _check_editable(mesh_obj)
    curve, raw = _owned_curve(mesh_obj), mesh_obj[_STATE]
    _check_fit_curve(mesh_obj, curve)
    _object_mode()  # Commit in Object Mode for global Undo of curve and metadata.
    _check_neutral_keys(mesh_obj, state.preview_name)
    if _topology(mesh_obj.data) != raw['topology'] or _digest(_basis(mesh_obj)) != raw['basis_hash']:
        raise ValueError('The source mesh changed. Undo the mesh edit before applying the curve fit.')
    arrays, tilts = _curve_arrays(curve), _curve_tilts(curve)
    if len(arrays) < 4:
        raise ValueError('Keep at least four points on the closed mouth curve, or Cancel Fit.')
    if not np.isfinite(arrays).all() or not np.isfinite(tilts).all():
        raise ValueError('The curve contains non-finite coordinates or tilt. Undo the edit or Cancel.')
    relative = curve.matrix_parent_inverse @ curve.matrix_basis
    if relative.determinant() == 0:
        raise ValueError('The control curve has zero scale. Restore its scale or Cancel.')
    local = _transform(arrays.reshape((-1, 3)), relative).reshape(arrays.shape)
    types = _handle_types(curve)
    # Bake object transforms into the curve so Reset and symmetry use mesh space.
    # Under a nonuniform transform, freezing handles preserves the exact fit.
    if not np.allclose(np.asarray(relative), np.eye(4), rtol=0, atol=1e-7):
        types = [0] * len(types)
    old_raw = raw.to_dict()
    old_count = state.control_count
    parameters = list(raw['parameters'])
    if len(arrays) != old_count:
        world = _transform(local.reshape((-1, 3)), mesh_obj.matrix_world).reshape(local.shape)
        reference = _transform(np.asarray(raw['rest_samples']).reshape((-1, 3)), mesh_obj.matrix_world)
        parameters = geometry.reparameterize_bezier(*world.transpose(1, 0, 2), reference).tolist()
    old_data = curve.data
    old_matrix, old_inverse = curve.matrix_basis.copy(), curve.matrix_parent_inverse.copy()
    new_data = old_data.copy()
    try:
        curve.data = new_data
        curve.matrix_parent_inverse = Matrix.Identity(4)
        curve.matrix_basis = Matrix.Identity(4)
        _restore_curve(curve, local, types, tilts)
        rest = _curve_arrays(curve)
        raw['parameters'] = parameters
        raw['rest_curve'] = rest.ravel().tolist()
        raw['rest_samples'] = geometry.evaluate_bezier(*rest.transpose(1, 0, 2), raw['parameters']).ravel().tolist()
        raw['rest_handle_types'] = types
        raw['rest_tilts'] = tilts.tolist()
        state.control_count = len(rest)
        if len(rest) != old_count:
            raw['corner_controls'] = []  # Custom fit may no longer have pinned corner controls.
        _clear_fit_metadata(mesh_obj)
        update_session(mesh_obj, force=True)
    except Exception:
        state.fit_editing = True
        state.control_count = old_count
        curve.data = old_data
        curve.matrix_parent_inverse, curve.matrix_basis = old_inverse, old_matrix
        mesh_obj[_STATE] = old_raw
        _CACHE.pop(state.token, None)
        if new_data.users == 0:
            bpy.data.curves.remove(new_data)
        raise
    backup = state.fit_backup
    state.fit_backup = None
    for data in (old_data, backup):
        if data.users == 0:
            bpy.data.curves.remove(data)


def cancel_curve_fit(mesh_obj):
    """Restore the original curve even after accidental point/spline deletion."""
    state = mesh_obj.curvemorph
    if not state.fit_editing or state.fit_backup is None:
        raise ValueError('There is no curve fit to cancel.')
    curve, raw = _owned_curve(mesh_obj), mesh_obj[_STATE]
    _object_mode()
    edited = curve.data
    curve.data = state.fit_backup
    state.fit_backup = None
    curve.parent, curve.parent_type = mesh_obj, 'OBJECT'
    curve.matrix_parent_inverse = Matrix(np.asarray(raw['fit_parent_inverse']).reshape(4, 4))
    curve.matrix_basis = Matrix(np.asarray(raw['fit_matrix']).reshape(4, 4))
    _clear_fit_metadata(mesh_obj)
    if edited.users == 0:
        bpy.data.curves.remove(edited)
    update_session(mesh_obj, force=True)


def reset_pose(mesh_obj):
    _check_not_fitting(mesh_obj)
    if mesh_obj.curvemorph.setup_editing:
        raise ValueError("Return to Edit Controls or Apply Setup before resetting the pose.")
    curve = _owned_curve(mesh_obj)
    _check_editable(mesh_obj)
    raw = mesh_obj[_STATE]
    rest = np.asarray(raw["rest_curve"], dtype=np.float64).reshape((-1, 3, 3))
    was_edit = curve.mode == 'EDIT'
    _object_mode()
    curve.matrix_basis = Matrix.Identity(4)
    # Older sessions retain their original automatic corners until rebuilt.
    if 'rest_handle_types' in raw:
        _restore_curve(curve, rest, raw['rest_handle_types'], raw['rest_tilts'])
    else:
        handles = {int(i): rest[int(i), 1:] for i in raw.get('corner_controls', [])}
        _set_curve(curve, rest[:, 0], handles)
    _CACHE.pop(mesh_obj.curvemorph.token, None)
    update_session(mesh_obj, force=True)
    if was_edit:
        edit_controls(bpy.context, mesh_obj)


def symmetrize_pose(mesh_obj, direction):
    _check_not_fitting(mesh_obj)
    if mesh_obj.curvemorph.setup_editing:
        raise ValueError("Resume Current Controls or Apply Setup before symmetrizing the pose.")
    _check_editable(mesh_obj)
    curve = _owned_curve(mesh_obj)
    _object_mode()  # Flush live edit handles; finish in Object Mode for global Undo.
    update_session(mesh_obj, force=True)
    relative = mesh_obj.matrix_world.inverted() @ curve.matrix_world
    if abs(relative.determinant()) < 1e-12:
        raise ValueError("The control curve has zero scale. Restore its scale before symmetrizing.")
    before = _curve_arrays(curve)
    before_tilts = _curve_tilts(curve)
    rest = np.asarray(mesh_obj[_STATE]['rest_curve'], dtype=np.float64).reshape((-1, 3, 3))
    local = _transform(before.reshape((-1, 3)), relative).reshape(before.shape)
    rest_tilts = np.asarray(mesh_obj[_STATE].get('rest_tilts', [0.0] * len(before_tilts)))
    mirrored, mirrored_tilts = geometry.symmetrize_bezier(rest, local, direction, tilts=before_tilts-rest_tilts)
    mirrored_tilts += rest_tilts
    result = _transform(mirrored.reshape((-1, 3)), relative.inverted()).reshape(before.shape)
    points = curve.data.splines[0].bezier_points
    old_types = [(p.handle_left_type, p.handle_right_type) for p in points]

    def write(arrays, tilts):
        # Freeze the copied tangents so AUTO cannot move the chosen source side
        # when its opposite neighbors change. V > Automatic can restore auto handles.
        for p in points:
            p.handle_left_type = p.handle_right_type = 'FREE'
        for p, values, tilt in zip(points, arrays, tilts):
            p.co, p.handle_left, p.handle_right = values
            p.tilt = tilt
        curve.data.update_tag()
        bpy.context.view_layer.update()

    try:
        write(result, mirrored_tilts)
        update_session(mesh_obj, force=True)
    except Exception:
        write(before, before_tilts)
        for p, (left, right) in zip(points, old_types):
            p.handle_left_type, p.handle_right_type = left, right
        curve.data.update_tag()
        _CACHE.pop(mesh_obj.curvemorph.token, None)
        update_session(mesh_obj, force=True)
        raise


def rebuild_controls(mesh_obj, control_count):
    raw = mesh_obj[_STATE]
    reconfigure(mesh_obj, list(raw["loop"]), list(raw.get("corners", [])), control_count)


def reconfigure(mesh_obj, loop, corners, control_count):
    _check_not_fitting(mesh_obj)
    """Prepare a new neutral layout, keeping the old curve/key on failure."""
    if not 4 <= control_count <= 64:
        raise ValueError("Choose between 4 and 64 controls.")
    _check_editable(mesh_obj)
    curve = _owned_curve(mesh_obj)
    preview = _preview(mesh_obj)
    _check_neutral_keys(mesh_obj, preview.name)
    _object_mode()
    raw = mesh_obj[_STATE]
    if _topology(mesh_obj.data) != raw["topology"] or _digest(_basis(mesh_obj)) != raw["basis_hash"]:
        raise ValueError("Source geometry changed. Undo the change or finish and recreate controls.")
    coordinates, parameters = _layout(mesh_obj, loop, control_count, corners)
    handles = geometry.corner_handles(_basis(mesh_obj)[loop], coordinates, parameters,
                                      [loop.index(i) for i in corners])
    binding = geometry.build_binding(_transform(_basis(mesh_obj), mesh_obj.matrix_world), _edges(mesh_obj.data), loop, mesh_obj.curvemorph.radius)
    _mask_weights(mesh_obj, binding["indices"])
    _twist_mask_weights(mesh_obj, binding['indices'])
    state = mesh_obj.curvemorph
    old_data, old_matrix = curve.data, curve.matrix_basis.copy()
    old_raw = raw.to_dict()
    old_count, old_editing = state.control_count, state.setup_editing
    old_preview, old_value = _coords(preview.data), preview.value
    new_data = old_data.copy()
    try:
        curve.data = new_data
        curve.matrix_basis = Matrix.Identity(4)
        _set_curve(curve, coordinates, handles)
        rest = _curve_arrays(curve)
        raw["loop"] = list(loop)
        raw["corners"] = list(corners)
        raw["corner_controls"] = list(handles)
        raw["parameters"] = parameters.tolist()
        raw["rest_curve"] = rest.ravel().tolist()
        for key in ('rest_handle_types', 'rest_tilts'):
            if key in raw:
                del raw[key]
        raw["rest_samples"] = geometry.evaluate_bezier(rest[:, 0], rest[:, 1], rest[:, 2], parameters).ravel().tolist()
        state.control_count = control_count
        state.setup_editing = False
        _CACHE.pop(state.token, None)
        update_session(mesh_obj, force=True, prepared_binding=binding)
    except Exception:
        curve.data = old_data
        curve.matrix_basis = old_matrix
        mesh_obj[_STATE] = old_raw
        state.control_count, state.setup_editing = old_count, old_editing
        preview.data.foreach_set('co', old_preview.ravel())
        preview.value = old_value
        _CACHE.pop(state.token, None)
        if new_data.users == 0:
            bpy.data.curves.remove(new_data)
        raise
    if old_data.users == 0:
        bpy.data.curves.remove(old_data)


def save_shape_key(mesh_obj, name, reset_after=True):
    _check_not_fitting(mesh_obj)
    if mesh_obj.curvemorph.setup_editing:
        raise ValueError("Return to Edit Controls or Apply Setup before saving a pose.")
    name = name.strip()
    if not name:
        raise ValueError("Enter a shape key name.")
    if name.startswith("__CurveMorph_"):
        raise ValueError("Choose a name without the reserved __CurveMorph_ prefix.")
    update_session(mesh_obj, force=True)
    coordinates = _coords(_preview(mesh_obj).data)
    if np.max(np.abs(coordinates - _basis(mesh_obj))) < 1e-8:
        raise ValueError("Move or tilt a control first; the current pose is neutral.")
    # Creating a key changes another ID. End in Object Mode so Blender records
    # a full undo step instead of only the curve's Edit Mode undo data.
    _object_mode()
    key = mesh_obj.shape_key_add(name=name, from_mix=False)
    try:
        key.data.foreach_set("co", coordinates.ravel())
        key.relative_key = mesh_obj.data.shape_keys.reference_key
        key.value = 0.0
    except Exception:
        mesh_obj.shape_key_remove(key)
        raise
    mesh_obj.curvemorph.last_saved_name = key.name
    if reset_after:
        reset_pose(mesh_obj)
    mesh_obj.data.update()
    return key


def finish_session(mesh_obj):
    """Remove only owned transient data; saved expression keys stay intact."""
    _check_editable(mesh_obj)
    _check_not_fitting(mesh_obj)
    state = mesh_obj.curvemorph
    token = state.token
    if not token:
        return
    curve = state.curve
    if curve and (curve.curvemorph_target != mesh_obj or curve.get(_OWNER) != token):
        raise ValueError("These controls belong to another mesh. Select the original target to finish.")
    _object_mode()
    preview = None
    try:
        preview = _preview(mesh_obj)
    except ValueError:
        pass
    raw = mesh_obj.get(_STATE)
    original_active = int(raw.get("original_active", 0)) if raw else 0
    created_basis = bool(raw.get("created_basis", False)) if raw else False
    state.token = ""
    _CACHE.pop(token, None)
    if preview:
        mesh_obj.shape_key_remove(preview)
    if curve:
        curve_data = curve.data
        bpy.data.objects.remove(curve, do_unlink=True)
        if curve_data.users == 0:
            bpy.data.curves.remove(curve_data)
    keys = mesh_obj.data.shape_keys
    if created_basis and keys and len(keys.key_blocks) == 1:
        mesh_obj.shape_key_remove(keys.reference_key)
    if _STATE in mesh_obj:
        del mesh_obj[_STATE]
    state.curve = None
    state.preview_name = ""
    state.error = ""
    state.setup_editing = False
    for scene in bpy.data.scenes:
        if scene.curvemorph_settings.target == mesh_obj:
            scene.curvemorph_settings.target = None
    mesh_obj.active_shape_key_index = min(original_active, max(0, len(mesh_obj.data.shape_keys.key_blocks) - 1)) if mesh_obj.data.shape_keys else 0
    _activate(bpy.context, mesh_obj)


def _update_all():
    from . import facial
    global _BUSY
    if _BUSY:
        return
    _BUSY = True
    try:
        for obj in list(bpy.data.objects):
            if obj.type != 'MESH':
                continue
            if obj.curvemorph.token:
                try:
                    if obj.mode == 'EDIT':
                        # Facial path selection shares this mesh; keep the old mouth
                        # neutral too, then invalidate so Object Mode resumes its pose.
                        key = _preview(obj)
                        if key.value:
                            key.value = 0
                        _CACHE.pop(obj.curvemorph.token, None)
                    else:
                        update_session(obj)
                except Exception as exc:
                    _set_error(obj, str(exc))
            facial.update_all(obj)
    finally:
        _BUSY = False


@persistent
def _depsgraph_update(_scene, _depsgraph):
    # Writes run on the main event-loop timer, outside dependency graph evaluation.
    for obj in bpy.data.objects:
        if obj.type == 'MESH' and obj.curvemorph.token and obj.mode == 'EDIT':
            _CACHE.pop(obj.curvemorph.token, None)


def _tick():
    _update_all()
    return 0.04


@persistent
def _invalidate(*_args):
    from . import facial
    _CACHE.clear()
    facial.CACHE.clear()


@persistent
def _save_preview(*_args):
    _update_all()


def register():
    for handlers, callback in (
        (bpy.app.handlers.load_post, _invalidate),
        (bpy.app.handlers.undo_post, _invalidate),
        (bpy.app.handlers.redo_post, _invalidate),
        (bpy.app.handlers.save_pre, _save_preview),
        (bpy.app.handlers.depsgraph_update_post, _depsgraph_update),
    ):
        if callback not in handlers:
            handlers.append(callback)
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.1, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    for handlers, callback in (
        (bpy.app.handlers.load_post, _invalidate),
        (bpy.app.handlers.undo_post, _invalidate),
        (bpy.app.handlers.redo_post, _invalidate),
        (bpy.app.handlers.save_pre, _save_preview),
        (bpy.app.handlers.depsgraph_update_post, _depsgraph_update),
    ):
        if callback in handlers:
            handlers.remove(callback)
    _CACHE.clear()
