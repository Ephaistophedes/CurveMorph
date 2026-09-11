"""Independent facial curves, additive shape-key previews and persistent ownership.

The original mouth session remains intact and appears as a legacy list entry.
New entries own their curve, rest metadata and preview; no active-entry swapping
is used by the evaluator, so every control contributes simultaneously.
"""
import uuid

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

from . import geometry, session

OWNER = '_face_pose_token'
RAW = '_face_pose_rest'
CACHE = {}


def target(context):
    obj = context.active_object
    if obj:
        if obj.type == 'MESH':
            return obj
        mesh = obj.curvemorph_target
        if mesh and mesh.type == 'MESH':
            if mesh.curvemorph.curve == obj or any(e.curve == obj for e in mesh.face_pose_curves):
                return mesh
    mesh = context.scene.curvemorph_settings.target
    return mesh if mesh and mesh.type == 'MESH' else None


def sync_legacy(mesh):
    entries = mesh.face_pose_curves
    for i in reversed(range(len(entries))):
        if entries[i].legacy and not mesh.curvemorph.token:
            entries.remove(i)
    if mesh.curvemorph.token and not any(e.legacy for e in entries):
        entry = entries.add()
        entry.name = 'Mouth'
        entry.legacy = True
    for entry in entries:
        if entry.legacy:
            entry.curve = mesh.curvemorph.curve
            entry.token = mesh.curvemorph.token


def active(mesh):
    if mesh is None or not mesh.face_pose_curves:
        raise ValueError('Create or select a facial control first.')
    return mesh.face_pose_curves[min(mesh.face_pose_index, len(mesh.face_pose_curves) - 1)]


def owned_curve(mesh, entry):
    curve = entry.curve
    if entry.legacy:
        return session._owned_curve(mesh)
    if (curve is None or curve.type != 'CURVE' or curve.curvemorph_target != mesh
            or curve.get(OWNER) != entry.token):
        raise ValueError('Control curve is missing or belongs to another mesh. Undo deletion or remove this entry.')
    return curve


def preview(mesh, entry):
    if entry.legacy:
        return session._preview(mesh)
    keys = mesh.data.shape_keys
    key = keys.key_blocks.get('__FacePose_' + entry.token) if keys else None
    if key is None:
        raise ValueError('The facial preview key is missing. Undo deletion or remove this control.')
    return key


def preview_names(mesh):
    return {'__FacePose_' + e.token for e in mesh.face_pose_curves
            if not e.legacy and e.token and e.curve and e.curve.get(OWNER) == e.token
            and e.curve.curvemorph_target == mesh}


def check_keys(mesh):
    session._check_editable(mesh)
    session._check_neutral_keys(mesh, mesh.curvemorph.preview_name if mesh.curvemorph.token else '')


def arrays(curve):
    if len(curve.data.splines) != 1 or curve.data.splines[0].type != 'BEZIER':
        raise ValueError('Keep one Bézier spline per control; undo spline changes.')
    values = np.array([(tuple(p.co), tuple(p.handle_left), tuple(p.handle_right))
                       for p in curve.data.splines[0].bezier_points], dtype=float)
    if len(values) < 2 or not np.isfinite(values).all():
        raise ValueError('The curve needs at least two finite control points.')
    return values


def set_curve(curve, values, closed, types=None, tilts=None):
    interpolation = curve.data.splines[0].tilt_interpolation if curve.data.splines else 'LINEAR'
    curve.data.splines.clear()
    spline = curve.data.splines.new('BEZIER')
    spline.tilt_interpolation = interpolation
    spline.use_cyclic_u = closed
    spline.bezier_points.add(len(values) - 1)
    coordinates_only = values.ndim == 2
    for i, point in enumerate(spline.bezier_points):
        point.handle_left_type = point.handle_right_type = 'FREE'
        if coordinates_only:
            point.co = values[i]
        else:
            point.co, point.handle_left, point.handle_right = values[i]
        point.tilt = float(tilts[i]) if tilts is not None else 0
        point.select_control_point = point.select_left_handle = point.select_right_handle = False
    for i, point in enumerate(spline.bezier_points):
        point.handle_left_type = session._HANDLE_TYPES[int(types[2*i])] if types is not None else ('AUTO' if coordinates_only else 'FREE')
        point.handle_right_type = session._HANDLE_TYPES[int(types[2*i+1])] if types is not None else ('AUTO' if coordinates_only else 'FREE')
    curve.data.update_tag()
    bpy.context.view_layer.update()


def remember_rest(curve, raw):
    values = arrays(curve)
    raw['rest_curve'] = values.ravel().tolist()
    raw['types'] = session._handle_types(curve)
    raw['tilts'] = session._curve_tilts(curve).tolist()
    raw['rest_samples'] = geometry.evaluate_bezier(values[:, 0], values[:, 1], values[:, 2],
                                                  raw['parameters'], closed=bool(raw['closed'])).ravel().tolist()


def create(mesh, points, seeds, parameters=None, closed=False, name='Face Control', source='EDGES',
           count=6, radius=0, falloff=1, values=None, rest_tilts=None, types=None,
           mask_group='', twist_mask_group='', falloff_type='SMOOTH', random_seed=0):
    """Create one fully owned entry; roll back all new data on failure."""
    check_keys(mesh)
    session._object_mode()
    if not name.strip():
        raise ValueError('Enter a control name.')
    basis = session._basis(mesh)
    points = geometry._points(points)
    world = session._transform(points, mesh.matrix_world)
    controls = None
    if values is None:
        controls, path_parameters = geometry.path_layout(world, count, closed)
    else:
        # Mirroring an already fitted curve must preserve all its points, even
        # when subdivision took it beyond the initial creation slider's range.
        values = np.asarray(values, dtype=float)
        if (values.ndim != 3 or values.shape[1:] != (3, 3)
                or len(values) < (4 if closed else 2) or not np.isfinite(values).all()
                or parameters is None):
            raise ValueError('Provide a valid fitted Bézier layout and its mesh correspondence.')
    if parameters is None:
        parameters = path_parameters
    parameters = np.asarray(parameters, dtype=float)
    if len(parameters) != len(seeds) or not np.isfinite(parameters).all():
        raise ValueError('Each surface seed needs a finite curve parameter.')
    if radius == 0:
        radius = max(np.linalg.norm(np.ptp(world, axis=0)) * .25, 1e-6)
    geometry.build_binding(session._transform(basis, mesh.matrix_world), session._edges(mesh.data),
                           seeds, radius, closed=closed, check_edges=source == 'EDGES')
    for group in (mask_group, twist_mask_group):
        if group and mesh.vertex_groups.get(group) is None:
            raise ValueError('Choose an existing vertex group.')
    token = uuid.uuid4().hex
    curve = data = key = entry = None
    created_basis = mesh.data.shape_keys is None
    try:
        if created_basis:
            mesh.shape_key_add(name='Basis', from_mix=False)
        key = mesh.shape_key_add(name='__FacePose_' + token, from_mix=False)
        key.relative_key = mesh.data.shape_keys.reference_key
        data = bpy.data.curves.new(name, 'CURVE')
        data.dimensions = '3D'
        data.resolution_u = 16
        curve = bpy.data.objects.new(name, data)
        bpy.context.scene.collection.objects.link(curve)
        curve.parent = mesh
        curve.matrix_parent_inverse = Matrix.Identity(4)
        curve.matrix_basis = Matrix.Identity(4)
        curve.show_in_front = True
        curve.hide_render = True
        curve.curvemorph_target = mesh
        curve[OWNER] = token
        local = session._transform(controls, mesh.matrix_world.inverted()) if controls is not None else None
        set_curve(curve, local if values is None else values, closed, types, rest_tilts)
        raw = {'seeds': list(map(int, seeds)), 'parameters': parameters.tolist(), 'closed': closed,
               'source': source, 'topology': session._topology(mesh.data), 'basis_hash': session._digest(basis)}
        remember_rest(curve, raw)
        curve[RAW] = raw
        sync_legacy(mesh)
        entry = mesh.face_pose_curves.add()
        entry.name = curve.name
        entry.token, entry.curve = token, curve
        entry.radius, entry.falloff = radius, falloff
        entry.falloff_type, entry.random_seed = falloff_type, random_seed
        entry.mask_group, entry.twist_mask_group = mask_group, twist_mask_group
        mesh.face_pose_index = len(mesh.face_pose_curves) - 1
        bpy.context.scene.curvemorph_settings.target = mesh
        bpy.context.scene.face_pose_settings.ui_mode = 'POSE'
        update(mesh, entry, force=True)
        session._activate(bpy.context, curve)
        return entry
    except Exception:
        CACHE.pop(token, None)
        for i in reversed(range(len(mesh.face_pose_curves))):
            if mesh.face_pose_curves[i].token == token:
                mesh.face_pose_curves.remove(i)
        if curve:
            bpy.data.objects.remove(curve, do_unlink=True)
        if data and data.users == 0:
            bpy.data.curves.remove(data)
        if key:
            mesh.shape_key_remove(key)
        if created_basis and mesh.data.shape_keys and len(mesh.data.shape_keys.key_blocks) == 1:
            mesh.shape_key_remove(mesh.data.shape_keys.reference_key)
        raise


def create_edges(context):
    mesh = context.active_object
    if not mesh or mesh.type != 'MESH' or mesh.mode != 'EDIT':
        raise ValueError('Select one open edge chain or closed loop on the mesh in Edit Mode.')
    bm = bmesh.from_edit_mesh(mesh.data)
    bm.verts.index_update()
    seeds, closed = geometry.order_path(len(bm.verts), [tuple(v.index for v in e.verts) for e in bm.edges if e.select])
    if set(seeds) != {v.index for v in bm.verts if v.select}:
        raise ValueError('Select only the edge path, without extra vertices.')
    if mesh.active_shape_key_index != 0:
        raise ValueError('Select the Basis shape key before creating a control.')
    session._object_mode()
    settings = context.scene.face_pose_settings
    return create(mesh, session._basis(mesh)[seeds], seeds, closed=closed, name=settings.name,
                  count=max(4 if closed else 2, settings.count), radius=settings.radius)


def tree_for(points):
    tree = KDTree(len(points))
    for i, point in enumerate(points):
        tree.insert(point, i)
    tree.balance()
    return tree


def stroke_specs(mesh, grease, count):
    """Read visible current-frame strokes and project to the neutral surface.

    Drawing attributes are the Blender 5 API; input drawings are never modified.
    Selected strokes win; if none are selected, import all visible strokes.
    """
    if grease is None or grease.type != 'GREASEPENCIL':
        raise ValueError('Choose a Grease Pencil object containing face strokes.')
    basis = session._basis(mesh)
    world = session._transform(basis, mesh.matrix_world)
    bvh = BVHTree.FromPolygons(world.tolist(), [list(p.vertices) for p in mesh.data.polygons])
    tree = tree_for(world)
    strokes = []
    for layer in grease.data.layers:
        if layer.hide:
            continue
        frame = layer.current_frame()
        if frame is None:
            continue
        drawing = frame.drawing
        position = drawing.attributes.get('position')
        if position is None:
            continue
        positions = np.empty(len(position.data) * 3)
        position.data.foreach_get('vector', positions)
        positions = session._transform(positions.reshape((-1, 3)), grease.matrix_world)
        offsets = [item.value for item in drawing.curve_offsets]
        cyclic = drawing.attributes.get('cyclic')
        selection = drawing.attributes.get('.selection')
        for i, (start, end) in enumerate(zip(offsets, offsets[1:])):
            if end - start < 2:
                continue
            selected = False
            if selection:
                selected = (bool(selection.data[i].value) if selection.domain == 'CURVE'
                            else any(selection.data[j].value for j in range(start, end)))
            closed = bool(cyclic.data[i].value) if cyclic else False
            strokes.append((positions[start:end], closed, selected))
    if any(s[2] for s in strokes):
        strokes = [s for s in strokes if s[2]]
    if not strokes:
        raise ValueError('No usable strokes on visible layers at the current frame.')
    specs = []
    for points, closed, _selected in strokes:
        projected = []
        # Densify sparse drawings so mesh binding does not skip the stroke interior.
        lengths = np.linalg.norm(np.diff(points, axis=0), axis=1)
        samples = min(4096, max(len(points), count * 16, int(lengths.sum() / max(np.linalg.norm(np.ptp(world, axis=0)) / 500, 1e-8))))
        if closed:
            dense = geometry.resample_loop(points, samples)
        else:
            distance = np.r_[0, np.cumsum(lengths)]
            if distance[-1] <= 0:
                raise ValueError('A Grease Pencil stroke has zero length.')
            dense = np.column_stack([np.interp(np.linspace(0, distance[-1], samples), distance, points[:, i]) for i in range(3)])
        for point in dense:
            hit = bvh.find_nearest(Vector(point))
            if hit[0] is None:
                raise ValueError('The target mesh needs faces for surface projection.')
            projected.append(tuple(hit[0]))
        projected = np.asarray(projected)
        _, params = geometry.path_layout(projected, max(4 if closed else 2, count), closed)
        seeds, seed_parameters, seen = [], [], set()
        for point, param in zip(projected, params):
            index = tree.find(Vector(point))[1]
            if index not in seen:
                seen.add(index)
                seeds.append(index)
                seed_parameters.append(param)
        if len(seeds) < (4 if closed else 2):
            raise ValueError('A stroke covers too few mesh vertices. Draw a longer stroke or use denser topology.')
        specs.append((session._transform(projected, mesh.matrix_world.inverted()), seeds, seed_parameters, closed))
    return specs


def create_strokes(context):
    mesh = target(context)
    if mesh is None:
        raise ValueError('Choose the character mesh in Target before importing strokes.')
    check_keys(mesh)
    settings = context.scene.face_pose_settings
    grease = settings.grease
    if context.active_object and context.active_object.type == 'GREASEPENCIL':
        grease = context.active_object
    session._object_mode()
    specs = stroke_specs(mesh, grease, settings.count)
    tokens = []
    try:
        for i, (points, seeds, params, closed) in enumerate(specs):
            entry = create(mesh, points, seeds, params, closed, settings.name if len(specs) == 1 else f'{settings.name} {i+1}',
                           source='GREASE', count=max(4 if closed else 2, settings.count), radius=settings.radius)
            tokens.append(entry.token)
    except Exception:
        for token in reversed(tokens):
            remove(mesh, next(e for e in mesh.face_pose_curves if e.token == token))
        raise
    return len(tokens)


def update(mesh, entry, force=False):
    if entry.legacy:
        return session.update_session(mesh, force=force)
    curve = owned_curve(mesh, entry)
    key = preview(mesh, entry)
    if mesh.mode == 'EDIT' or entry.fitting:
        if key.value != 0:
            key.value = 0
        return
    check_keys(mesh)
    raw = curve.get(RAW)
    if raw is None:
        raise ValueError('Rest data is missing. Remove and recreate this control.')
    values = arrays(curve)
    rest = np.asarray(raw['rest_curve']).reshape((-1, 3, 3))
    closed = bool(raw['closed'])
    if values.shape != rest.shape or curve.data.splines[0].use_cyclic_u != closed:
        raise ValueError('Control topology changed outside Fit. Undo it, then use Adjust Neutral Fit to add or remove points.')
    relative = (curve.matrix_parent_inverse @ curve.matrix_basis if curve.parent == mesh and curve.parent_type == 'OBJECT' and not curve.constraints
                else mesh.matrix_world.inverted() @ curve.matrix_world)
    metric = np.asarray(mesh.matrix_world)[:3, :3].tobytes()
    tilts = session._curve_tilts(curve) - np.asarray(raw['tilts'])
    if not np.isfinite(tilts).all():
        raise ValueError('Clear non-finite tilt with Alt+T.')
    interpolation = curve.data.splines[0].tilt_interpolation
    signature = (values.tobytes(), tilts.tobytes(), np.asarray(relative).tobytes(), metric,
                 entry.radius, entry.falloff, entry.falloff_type, entry.random_seed, entry.enabled, interpolation)
    cache = CACHE.get(entry.token)
    if cache is None or force or cache['metric'] != metric or cache['radius'] != entry.radius:
        basis = session._basis(mesh)
        if session._topology(mesh.data) != raw['topology'] or session._digest(basis) != raw['basis_hash']:
            raise ValueError('Mesh topology or Basis changed. Undo the edit or recreate this control.')
        binding = geometry.build_binding(session._transform(basis, mesh.matrix_world), session._edges(mesh.data),
                                         list(raw['seeds']), entry.radius, closed=closed, check_edges=raw['source'] == 'EDGES')
        cache = {'basis': basis, 'binding': binding, 'metric': metric, 'radius': entry.radius}
        CACHE[entry.token] = cache
    binding = cache['binding']
    mask = session._group_weights(mesh, binding['indices'], entry.mask_group, 'Influence group is missing. Choose another group or clear the field.')
    twist = session._group_weights(mesh, binding['indices'], entry.twist_mask_group, 'Twist group is missing. Choose another group or clear the field.')
    signature += (mask.tobytes(), twist.tobytes())
    if not force and cache.get('signature') == signature and key.value == float(entry.enabled) and not key.mute:
        entry.error = ''
        return
    posed = session._transform(values.reshape((-1, 3)), relative).reshape(values.shape)
    parameters = np.asarray(raw['parameters'])
    current = geometry.evaluate_bezier(posed[:, 0], posed[:, 1], posed[:, 2], parameters, closed)
    delta = current - np.asarray(raw['rest_samples']).reshape((-1, 3))
    displacements = geometry.apply_binding(binding, delta, entry.radius, entry.falloff, entry.falloff_type, entry.random_seed)
    if np.any(tilts):
        offsets = cache['basis'][binding['indices'], None, :] - cache['basis'][list(raw['seeds'])][binding['sources']]
        tangent = geometry.bezier_tangents(posed[:, 0], posed[:, 1], posed[:, 2], parameters, average_joints=True, closed=closed)
        angles = geometry.interpolate_tilt(tilts, parameters, interpolation, closed=closed)
        displacements += geometry.apply_tilt(binding, offsets, tangent, angles, entry.radius, entry.falloff,
                                             entry.falloff_type, entry.random_seed) * twist[:, None]
    result = cache['basis'].copy()
    result[binding['indices']] += displacements * mask[:, None]
    if not np.isfinite(result).all():
        raise ValueError('The pose produced non-finite mesh coordinates. Undo the last edit.')
    key.data.foreach_set('co', result.ravel())
    key.value, key.mute = float(entry.enabled), False
    mesh.data.update()
    cache['signature'] = signature
    entry.error = ''


def update_all(mesh):
    sync_legacy(mesh)
    for entry in mesh.face_pose_curves:
        if entry.legacy:
            continue
        try:
            update(mesh, entry)
        except Exception as error:
            if entry.error != str(error):
                entry.error = str(error)
            try:
                key = preview(mesh, entry)
                if key.value:
                    key.value = 0
            except ValueError:
                pass


def reset(mesh, entry):
    if entry.legacy:
        return session.reset_pose(mesh)
    if entry.fitting:
        raise ValueError('Apply or cancel the curve fit first.')
    curve = owned_curve(mesh, entry)
    raw = curve[RAW]
    session._object_mode()
    curve.parent = mesh
    curve.matrix_parent_inverse = Matrix.Identity(4)
    curve.matrix_basis = Matrix.Identity(4)
    set_curve(curve, np.asarray(raw['rest_curve']).reshape((-1, 3, 3)), bool(raw['closed']), raw['types'], raw['tilts'])
    CACHE.pop(entry.token, None)
    update(mesh, entry, force=True)


def edit(context, mesh, entry):
    if entry.legacy:
        return session.edit_controls(context, mesh)
    session._activate(context, owned_curve(mesh, entry))
    update(mesh, entry, force=True)
    bpy.ops.object.mode_set(mode='EDIT')


def fit(mesh, entry, action):
    if entry.legacy:
        if action == 'BEGIN':
            return session.begin_curve_fit(bpy.context, mesh)
        return session.cancel_curve_fit(mesh) if action == 'CANCEL' else session.apply_curve_fit(mesh)
    curve = owned_curve(mesh, entry)
    if curve.parent != mesh or curve.parent_type != 'OBJECT' or curve.constraints:
        raise ValueError('Keep the control parented to its target without constraints while fitting.')
    raw = curve[RAW]
    session._object_mode()
    if action == 'BEGIN':
        update(mesh, entry, force=True)
        if np.max(np.abs(session._coords(preview(mesh, entry).data) - session._basis(mesh))) > 1e-8:
            raise ValueError('Save or reset this control pose before adjusting its neutral fit.')
        if entry.fitting:
            raise ValueError('This control is already being fitted.')
        curve['_face_fit_backup'] = {'values': arrays(curve).ravel().tolist(), 'types': session._handle_types(curve),
                                     'tilts': session._curve_tilts(curve).tolist(), 'matrix': np.asarray(curve.matrix_basis).ravel().tolist(),
                                     'parent_inverse': np.asarray(curve.matrix_parent_inverse).ravel().tolist(),
                                     'interpolation': curve.data.splines[0].tilt_interpolation}
        entry.fitting = True
        preview(mesh, entry).value = 0
        edit(bpy.context, mesh, entry)
        return
    if not entry.fitting:
        raise ValueError('Start Adjust Curve Fit first.')
    if action == 'APPLY':
        check_keys(mesh)
        if session._topology(mesh.data) != raw['topology'] or session._digest(session._basis(mesh)) != raw['basis_hash']:
            raise ValueError('The target topology or Basis changed. Undo the mesh edit before applying the fit.')
        values = arrays(curve)
        closed = bool(raw['closed'])
        if curve.data.splines[0].use_cyclic_u != closed:
            raise ValueError('Keep the original open/closed setting while fitting, or Cancel Fit.')
        if len(values) < (4 if closed else 2):
            raise ValueError('Keep at least four points on a closed curve or two on an open curve.')
        relative = curve.matrix_parent_inverse @ curve.matrix_basis
        if relative.determinant() == 0:
            raise ValueError('The curve has zero scale. Restore its scale or Cancel Fit.')
        values = session._transform(values.reshape((-1, 3)), relative).reshape(values.shape)
        types, tilts = session._handle_types(curve), session._curve_tilts(curve)
        if not np.isfinite(tilts).all():
            raise ValueError('Clear non-finite tilt before applying the fit.')
        if not np.allclose(np.asarray(relative), np.eye(4), atol=1e-7):
            types = [0] * len(types)
        updated = raw.to_dict()
        if values.size != len(raw['rest_curve']):
            world = session._transform(values.reshape((-1, 3)), mesh.matrix_world).reshape(values.shape)
            reference = session._transform(np.asarray(raw['rest_samples']).reshape((-1, 3)), mesh.matrix_world)
            updated['parameters'] = geometry.reparameterize_bezier(*world.transpose(1, 0, 2), reference, closed).tolist()
        # Keep the edited spline and the original Cancel backup until validation
        # and preview generation both succeed, including after topology changes.
        old_data, old_raw = curve.data, raw.to_dict()
        old_matrix, old_inverse = curve.matrix_basis.copy(), curve.matrix_parent_inverse.copy()
        new_data = old_data.copy()
        try:
            curve.data = new_data
            curve.matrix_parent_inverse = Matrix.Identity(4)
            curve.matrix_basis = Matrix.Identity(4)
            set_curve(curve, values, closed, types, tilts)
            remember_rest(curve, updated)
            curve[RAW] = updated
            entry.fitting = False
            CACHE.pop(entry.token, None)
            update(mesh, entry, force=True)
        except Exception:
            entry.fitting = True
            curve.data, curve[RAW] = old_data, old_raw
            curve.matrix_basis, curve.matrix_parent_inverse = old_matrix, old_inverse
            CACHE.pop(entry.token, None)
            if new_data.users == 0:
                bpy.data.curves.remove(new_data)
            raise
        if old_data.users == 0:
            bpy.data.curves.remove(old_data)
    else:
        backup = curve['_face_fit_backup']
        curve.parent = mesh
        curve.matrix_parent_inverse = Matrix(np.asarray(backup['parent_inverse']).reshape((4, 4)).tolist())
        curve.matrix_basis = Matrix(np.asarray(backup['matrix']).reshape((4, 4)).tolist())
        set_curve(curve, np.asarray(backup['values']).reshape((-1, 3, 3)), bool(raw['closed']), backup['types'], backup['tilts'])
        if 'interpolation' in backup:
            curve.data.splines[0].tilt_interpolation = backup['interpolation']
    del curve['_face_fit_backup']
    entry.fitting = False
    CACHE.pop(entry.token, None)
    if action != 'APPLY':
        update(mesh, entry, force=True)


def mirrored_name(name):
    """Use Blender's side-name conventions, retaining numbered suffixes."""
    flipped = bpy.utils.flip_name(name)
    return flipped if flipped != name else name + '.Mirror'


def mirror(mesh, entry, axis='X', tolerance=0.001, mirror_groups=True):
    """Reflect rest and pose, preserving spline direction (therefore negate tilt)."""
    session._object_mode()
    check_keys(mesh)
    if entry.fitting or (entry.legacy and mesh.curvemorph.fit_editing):
        raise ValueError('Apply or cancel curve fit before mirroring.')
    curve = owned_curve(mesh, entry)
    update(mesh, entry, force=True)
    if entry.legacy:
        raw = mesh[session._STATE]
        seeds, closed, source = list(raw['loop']), True, 'EDGES'
        state = mesh.curvemorph
        mask = state.mask_group if state.use_mask else ''
        rest_tilts = np.asarray(raw.get('rest_tilts', [0.] * state.control_count))
        # Free handles preserve the stored rest geometry exactly, including old mouth sessions.
        rest_types = [0] * (state.control_count * 2)
    else:
        raw = curve[RAW]
        seeds, closed, source = list(raw['seeds']), bool(raw['closed']), raw['source']
        state, mask = entry, entry.mask_group
        rest_tilts, rest_types = np.asarray(raw['tilts']), raw['types']
    reflection = np.ones(3)
    reflection['XYZ'.index(axis)] = -1
    basis = session._basis(mesh)
    tree = tree_for(basis)
    mapping = {}
    def reflected_index(i):
        if i not in mapping:
            _, j, distance = tree.find(Vector(basis[i] * reflection))
            if distance > tolerance:
                raise ValueError(f'No mirrored vertex within {tolerance:g} local units. Increase Match Distance or check mesh symmetry.')
            mapping[i] = j
        return mapping[i]
    mirrored_seeds = [reflected_index(i) for i in seeds]
    if len(set(mirrored_seeds)) != len(seeds):
        raise ValueError('Mirrored seeds overlap. Reduce Match Distance or refine the mesh.')
    group_specs = {}
    if mirror_groups:
        for name in set((mask, state.twist_mask_group)) - {''}:
            group = mesh.vertex_groups.get(name)
            if group is None:
                raise ValueError('A source vertex group is missing.')
            weights = []
            targets = set()
            for vertex in mesh.data.vertices:
                weight = next((g.weight for g in vertex.groups if g.group == group.index), 0)
                if weight > 0:
                    j = reflected_index(vertex.index)
                    if j in targets:
                        raise ValueError('Mirrored group vertices overlap. Refine the mesh symmetry.')
                    targets.add(j)
                    weights.append((j, weight))
            group_specs[name] = weights
    rest = np.asarray(raw['rest_curve']).reshape((-1, 3, 3)) * reflection
    relative = mesh.matrix_world.inverted() @ curve.matrix_world
    posed = arrays(curve)
    posed = session._transform(posed.reshape((-1, 3)), relative).reshape(posed.shape) * reflection
    posed_tilts = -session._curve_tilts(curve)
    new_groups, names, new_entry = [], {}, None
    try:
        for name, weights in group_specs.items():
            group = mesh.vertex_groups.new(name=mirrored_name(name))
            new_groups.append(group)
            names[name] = group.name
            for i, weight in weights:
                group.add([i], weight, 'REPLACE')
        new_entry = create(mesh, basis[mirrored_seeds], mirrored_seeds, list(raw['parameters']), closed,
                           mirrored_name(entry.name), source=source, count=len(rest), radius=state.radius,
                           falloff=state.falloff, values=rest, rest_tilts=-rest_tilts, types=rest_types,
                           mask_group=names.get(mask, mask), twist_mask_group=names.get(state.twist_mask_group, state.twist_mask_group),
                           falloff_type=state.falloff_type, random_seed=state.random_seed)
        # Preserve a deliberately zero-radius control; create's zero means auto-size.
        new_entry.radius = state.radius
        set_curve(new_entry.curve, posed, closed, tilts=posed_tilts)
        update(mesh, new_entry, force=True)
        return new_entry
    except Exception:
        if new_entry:
            remove(mesh, new_entry)
        for group in new_groups:
            mesh.vertex_groups.remove(group)
        raise


def save(mesh, name, reset_after=True, only_active=False):
    session._object_mode()
    check_keys(mesh)
    sync_legacy(mesh)
    name = name.strip()
    if not name or name.startswith(('__CurveMorph_', '__FacePose_')):
        raise ValueError('Enter a shape-key name without a reserved preview prefix.')
    entries = [active(mesh)] if only_active else list(mesh.face_pose_curves)
    basis = session._basis(mesh)
    result = basis.copy()
    for entry in entries:
        if entry.fitting or (entry.legacy and (mesh.curvemorph.fit_editing or mesh.curvemorph.setup_editing)):
            raise ValueError('Finish curve fit or mouth setup before saving the face pose.')
        update(mesh, entry, force=True)
        key = preview(mesh, entry)
        result += (session._coords(key.data) - basis) * key.value
    if np.max(np.abs(result - basis)) < 1e-8:
        raise ValueError('Move or tilt an enabled control before saving.')
    key = mesh.shape_key_add(name=name, from_mix=False)
    key.data.foreach_set('co', result.ravel())
    key.relative_key, key.value = mesh.data.shape_keys.reference_key, 0
    if reset_after:
        for entry in entries:
            reset(mesh, entry)
    mesh.data.update()
    return key


def remove(mesh, entry):
    session._check_editable(mesh)
    session._object_mode()
    token = entry.token
    if entry.legacy:
        session.finish_session(mesh)
    else:
        curve = entry.curve
        if curve:
            owned_curve(mesh, entry)
        keys = mesh.data.shape_keys
        key = keys.key_blocks.get('__FacePose_' + token) if keys else None
        if key:
            mesh.shape_key_remove(key)
        if curve:
            data = curve.data
            bpy.data.objects.remove(curve, do_unlink=True)
            if data.users == 0:
                bpy.data.curves.remove(data)
    CACHE.pop(token, None)
    for i in reversed(range(len(mesh.face_pose_curves))):
        if mesh.face_pose_curves[i].token == token:
            mesh.face_pose_curves.remove(i)
    mesh.face_pose_index = min(mesh.face_pose_index, max(0, len(mesh.face_pose_curves) - 1))
    session._activate(bpy.context, mesh)
    bpy.context.scene.curvemorph_settings.target = mesh


class FacePoseCurve(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name='Name', default='Face Control')
    token: bpy.props.StringProperty(options={'HIDDEN'})
    curve: bpy.props.PointerProperty(type=bpy.types.Object)
    legacy: bpy.props.BoolProperty(default=False, options={'HIDDEN'})
    enabled: bpy.props.BoolProperty(name='Enabled', default=True, description='Include this control in live preview and saved poses')
    mask_group: bpy.props.StringProperty(name='Influence Group', description='Blank uses distance falloff without a group mask')
    twist_mask_group: bpy.props.StringProperty(name='Twist Mask')
    radius: bpy.props.FloatProperty(name='Influence Distance', default=.05, min=0, subtype='DISTANCE')
    falloff: bpy.props.FloatProperty(name='Falloff Strength', default=1, min=.2, max=4)
    falloff_type: bpy.props.EnumProperty(name='Falloff Type', items=[(v, v.replace('_', ' ').title(), '') for v in sorted(geometry.FALLOFF_TYPES)], default='SMOOTH')
    random_seed: bpy.props.IntProperty(name='Random Seed', default=0, min=0)
    fitting: bpy.props.BoolProperty(default=False, options={'HIDDEN'})
    error: bpy.props.StringProperty(options={'HIDDEN'})


class FacePoseSettings(bpy.types.PropertyGroup):
    show_add: bpy.props.BoolProperty(name='Add Controls', default=True, options={'HIDDEN'})
    ui_mode: bpy.props.EnumProperty(name='Workspace', items=[
        ('POSE', 'Pose', 'Select controls, adjust influence and save expressions'),
        ('ADD', 'Add Controls', 'Create controls from mesh edges or Grease Pencil')], default='POSE')
    source: bpy.props.EnumProperty(name='Create From', items=[
        ('EDGES', 'Mesh Edges', 'One open edge chain or closed loop'),
        ('GREASE', 'Grease Pencil', 'Current-frame strokes projected onto the target mesh'),
        ('MOUTH', 'Mouth with Corners', 'Closed mouth loop with two precise corner controls')], default='EDGES')
    name: bpy.props.StringProperty(name='New Control', default='Brow.L')
    count: bpy.props.IntProperty(name='Control Points', default=6, min=2, max=64)
    radius: bpy.props.FloatProperty(name='Influence Distance', default=0, min=0, subtype='DISTANCE')
    grease: bpy.props.PointerProperty(name='Grease Pencil', type=bpy.types.Object, poll=lambda self, obj: obj.type == 'GREASEPENCIL')
    axis: bpy.props.EnumProperty(name='Mirror Axis', items=[(a, a, 'Mesh local ' + a) for a in 'XYZ'], default='X')
    tolerance: bpy.props.FloatProperty(name='Match Distance', default=.001, min=.000001, precision=5,
                                     description='Maximum mirrored-vertex matching distance in mesh local units')
    mirror_groups: bpy.props.BoolProperty(name='Mirror Vertex Groups', default=True, description='Create independent reflected masks; original groups are retained')
    shape_name: bpy.props.StringProperty(name='Shape Key', default='Face Pose')
    reset_after: bpy.props.BoolProperty(name='Reset After Saving', default=True)
    only_active: bpy.props.BoolProperty(name='Save Active Control Only', default=False)


class FACEPOSE_OT_add_edges(bpy.types.Operator):
    bl_idname = 'facepose.add_edges'
    bl_label = 'Add from Selected Edges'
    bl_description = 'Create another independent control from one open chain or closed edge loop'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            create_edges(context)
            return {'FINISHED'}
        except (ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class FACEPOSE_OT_add_strokes(bpy.types.Operator):
    bl_idname = 'facepose.add_strokes'
    bl_label = 'Add from Grease Pencil'
    bl_description = 'Project current-frame strokes onto the neutral target surface; selected strokes, or all visible strokes if none selected'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            count = create_strokes(context)
            self.report({'INFO'}, f'Created {count} facial controls')
            return {'FINISHED'}
        except (ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class FACEPOSE_OT_action(bpy.types.Operator):
    bl_idname = 'facepose.action'
    bl_label = 'Facial Control'
    bl_options = {'REGISTER', 'UNDO'}
    action: bpy.props.EnumProperty(items=[(v, v.replace('_', ' ').title(), '') for v in
        ('EDIT', 'RESET', 'RESET_ALL', 'REMOVE', 'MIRROR', 'SAVE', 'FIT', 'APPLY_FIT', 'CANCEL_FIT', 'SELECT_MESH', 'REFRESH')])

    @classmethod
    def description(cls, context, properties):
        return {
            'EDIT': 'Select this control and enter Edit Mode to pose its points and handles',
            'RESET': 'Restore the selected control to its neutral fit',
            'RESET_ALL': 'Restore all controls to their neutral fits',
            'REMOVE': 'Remove this control and its temporary preview; saved shape keys and vertex groups are kept',
            'MIRROR': 'Create an independent mirrored control using the settings beside this button',
            'SAVE': 'Save the enabled control poses as a shape key; optionally save only the active control',
            'FIT': 'Adjust the resting curve without deforming the mesh; save or reset its pose first',
            'APPLY_FIT': 'Use this curve as the new neutral fit',
            'CANCEL_FIT': 'Restore the previous neutral curve',
            'SELECT_MESH': 'Enter mesh Edit Mode on the Basis to select another edge path',
            'REFRESH': 'Recalculate the selected control preview',
        }.get(properties.action, 'Facial control action')

    def execute(self, context):
        try:
            mesh = target(context)
            if mesh is None:
                raise ValueError('Choose the target character mesh first.')
            settings = context.scene.face_pose_settings
            sync_legacy(mesh)
            if self.action == 'SELECT_MESH':
                session._activate(context, mesh)
                mesh.active_shape_key_index = 0
                bpy.ops.object.mode_set(mode='EDIT')
                context.tool_settings.mesh_select_mode = (False, True, False)
                return {'FINISHED'}
            entry = active(mesh)
            if self.action == 'EDIT':
                edit(context, mesh, entry)
            elif self.action == 'RESET':
                reset(mesh, entry)
            elif self.action == 'RESET_ALL':
                for item in mesh.face_pose_curves:
                    reset(mesh, item)
            elif self.action == 'REMOVE':
                remove(mesh, entry)
            elif self.action == 'MIRROR':
                mirror(mesh, entry, settings.axis, settings.tolerance, settings.mirror_groups)
            elif self.action == 'SAVE':
                key = save(mesh, settings.shape_name, settings.reset_after, settings.only_active)
                self.report({'INFO'}, f'Saved {key.name}')
            elif self.action == 'REFRESH':
                session._object_mode()
                update(mesh, entry, force=True)
            else:
                fit(mesh, entry, {'FIT': 'BEGIN', 'APPLY_FIT': 'APPLY', 'CANCEL_FIT': 'CANCEL'}[self.action])
            return {'FINISHED'}
        except (ValueError, RuntimeError, KeyError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


CLASSES = (FacePoseCurve, FacePoseSettings, FACEPOSE_OT_add_edges, FACEPOSE_OT_add_strokes, FACEPOSE_OT_action)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.face_pose_curves = bpy.props.CollectionProperty(type=FacePoseCurve)
    bpy.types.Object.face_pose_index = bpy.props.IntProperty(default=0, min=0)
    bpy.types.Scene.face_pose_settings = bpy.props.PointerProperty(type=FacePoseSettings)


def unregister():
    CACHE.clear()
    del bpy.types.Scene.face_pose_settings
    del bpy.types.Object.face_pose_index
    del bpy.types.Object.face_pose_curves
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
