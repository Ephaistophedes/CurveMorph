"""Persistent, revisitable mouth-loop/corner selections and optional influence mask."""
import bmesh
import bpy
import numpy as np

from . import geometry, session

KEY = "_curvemorph_setup"


def resolve_target(context):
    obj = context.active_object
    if obj is not None:
        if obj.type == 'MESH':
            return obj
        target = obj.curvemorph_target
        if target and target.type == 'MESH' and target.curvemorph.curve == obj:
            return target
    target = context.scene.curvemorph_settings.target
    return target if target and target.type == 'MESH' else None


def read(mesh):
    if mesh is None:
        return None
    stored = mesh.get(KEY)
    if stored is not None:
        return stored
    # 3.0 sessions remain usable; offer their loop as the starting setup.
    old = mesh.get(session._STATE)
    if old:
        return {"loop": list(old["loop"]), "corners": list(old.get("corners", [])), "topology": old["topology"]}
    return None


def require(mesh, corners=False):
    stored = read(mesh)
    if not stored or not stored.get("loop"):
        raise ValueError("Store a closed mouth edge loop first.")
    if session._topology(mesh.data) != stored["topology"]:
        raise ValueError("The mesh topology changed. Store the mouth loop again before using this setup.")
    if corners and len(stored.get("corners", [])) != 2:
        raise ValueError("Select and store the two mouth corner vertices first.")
    return stored


def pending(mesh):
    stored = read(mesh)
    applied = mesh.get(session._STATE)
    return bool(stored and applied and (
        list(stored["loop"]) != list(applied["loop"])
        or list(stored.get("corners", [])) != list(applied.get("corners", []))
    ))


def _selected_mesh(context):
    mesh = context.active_object
    if mesh is None or mesh.type != 'MESH' or mesh.mode != 'EDIT':
        raise ValueError("Select vertices or edges on the target mesh in Edit Mode first.")
    session._check_editable(mesh)
    bm = bmesh.from_edit_mesh(mesh.data)
    bm.verts.index_update()
    return mesh, bm


def pause(mesh):
    """Neutralize only the temporary preview, preserving curve edits for resume."""
    session._check_editable(mesh)
    if mesh.curvemorph.token:
        session._owned_curve(mesh)
        preview = session._preview(mesh)
        mesh.curvemorph.setup_editing = True
        preview.value = 0
        mesh.data.update()
        session._CACHE.pop(mesh.curvemorph.token, None)


def begin_edit(context, mesh, kind):
    if mesh is None:
        raise ValueError("Select the character mesh first.")
    session._check_editable(mesh)
    # Flush edit data before validating stored indices.
    session._object_mode()
    stored = read(mesh)
    if kind == 'CORNERS' and stored:
        stored = require(mesh)
    if kind == 'LOOP' and stored and session._topology(mesh.data) != stored['topology']:
        stored = None
    if kind == 'CORNERS' and stored is None:
        raise ValueError("Store the mouth loop before selecting its corners.")
    group = mesh.vertex_groups.get(mesh.curvemorph.mask_group)
    if kind == 'PAINT' and group is None:
        raise ValueError("Create or choose an influence group first.")
    pause(mesh)
    session._activate(context, mesh)
    mesh.active_shape_key_index = 0
    context.scene.curvemorph_settings.target = mesh
    mesh.curvemorph.setup_kind = kind
    if kind == 'PAINT':
        mesh.vertex_groups.active_index = group.index
        bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
        return
    bpy.ops.object.mode_set(mode='EDIT')
    context.tool_settings.mesh_select_mode = (False, True, False) if kind == 'LOOP' else (True, False, False)
    bm = bmesh.from_edit_mesh(mesh.data)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    bm.select_mode = {'EDGE'} if kind == 'LOOP' else {'VERT'}
    for face in bm.faces:
        face.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for vertex in bm.verts:
        vertex.select_set(False)
    if kind == 'LOOP' and stored:
        loop = list(stored["loop"])
        pairs = {frozenset((a, b)) for a, b in zip(loop, loop[1:] + loop[:1])}
        for edge in bm.edges:
            if frozenset(v.index for v in edge.verts) in pairs:
                edge.hide_set(False)
                edge.select_set(True)
    elif kind == 'CORNERS':
        # The full stored loop must remain visible and pickable, but only the
        # two corners are selected. The cyan guide is a separate draw overlay.
        loop_set = set(stored['loop'])
        for index in loop_set:
            bm.verts[index].hide_set(False)
        for edge in bm.edges:
            if all(v.index in loop_set for v in edge.verts):
                edge.hide_set(False)
        for index in stored.get("corners", []):
            bm.verts[index].hide_set(False)
            bm.verts[index].select_set(True)
    elif kind == 'MASK' and group:
        deform = bm.verts.layers.deform.active
        if deform:
            for vertex in bm.verts:
                if vertex[deform].get(group.index, 0) > 0:
                    vertex.hide_set(False)
                    vertex.select_set(True)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(mesh.data, loop_triangles=False, destructive=False)
    from . import corner_overlay
    corner_overlay.redraw()


def capture_loop(context):
    mesh, bm = _selected_mesh(context)
    loop = geometry.order_closed_loop(len(bm.verts), [tuple(v.index for v in e.verts) for e in bm.edges if e.select])
    if {v.index for v in bm.verts if v.select} != set(loop):
        raise ValueError("Select only the closed mouth loop, with no extra vertices.")
    old = read(mesh)
    corners = list(old.get("corners", [])) if old else []
    session._object_mode()
    if (old and old['topology'] != session._topology(mesh.data)) or not all(index in loop for index in corners):
        corners = []
    elif corners:
        try:
            geometry.corner_layout(session._basis(mesh)[loop], 4, [loop.index(i) for i in corners])
        except ValueError:
            corners = []
    pause(mesh)
    mesh[KEY] = {"loop": loop, "corners": corners, "topology": session._topology(mesh.data)}
    context.scene.curvemorph_settings.target = mesh
    return mesh


def select_auto_corners(context):
    """Preselect local-X extremes on the stored neutral loop for confirmation."""
    mesh = resolve_target(context)
    if mesh is None:
        raise ValueError("Select the character mesh first.")
    session._check_editable(mesh)
    session._object_mode()
    stored = require(mesh)
    loop = list(stored['loop'])
    basis = session._basis(mesh)
    # Index sorting makes tied extrema deterministic, regardless of loop order.
    candidates = sorted(loop)
    x = basis[candidates, 0]
    if not np.isfinite(x).all() or np.ptp(x) <= 0:
        raise ValueError("The mouth loop has no usable local-X width. Select the corners manually.")
    corners = [candidates[int(np.argmin(x))], candidates[int(np.argmax(x))]]
    geometry.corner_layout(basis[loop], 4, [loop.index(i) for i in corners])
    begin_edit(context, mesh, 'CORNERS')
    bm = bmesh.from_edit_mesh(mesh.data)
    bm.verts.ensure_lookup_table()
    for vertex in bm.verts:
        vertex.select_set(False)
    bm.select_history.clear()
    for index in corners:
        bm.verts[index].select_set(True)
        bm.select_history.add(bm.verts[index])
    bm.select_flush_mode()
    bmesh.update_edit_mesh(mesh.data, loop_triangles=False, destructive=False)
    return corners


def capture_corners(context):
    mesh, bm = _selected_mesh(context)
    selected = sorted(v.index for v in bm.verts if v.select)
    stored = read(mesh)
    if len(selected) != 2:
        raise ValueError("Select exactly two vertices: one at each mouth corner.")
    if not stored or any(i not in stored["loop"] for i in selected):
        raise ValueError("Both corners must be vertices on the stored mouth loop.")
    session._object_mode()
    stored = require(mesh)
    loop = list(stored["loop"])
    geometry.corner_layout(session._basis(mesh)[loop], 4, [loop.index(i) for i in selected])
    pause(mesh)
    mesh[KEY] = {"loop": loop, "corners": selected, "topology": stored["topology"]}
    mesh.curvemorph.setup_kind = 'NONE'
    context.scene.curvemorph_settings.target = mesh
    return mesh


def create_mask(context, action='NEW'):
    mesh, bm = _selected_mesh(context)
    selected = [v.index for v in bm.verts if v.select]
    if not selected:
        raise ValueError("Select the vertices to affect first.")
    state = mesh.curvemorph
    group = mesh.vertex_groups.get(state.mask_group)
    if action != 'NEW' and group is None:
        raise ValueError("Choose an existing influence group first.")
    if action != 'NEW' and group.lock_weight:
        raise ValueError("The influence group is locked. Unlock it before changing its weights.")
    if action == 'NEW' and not state.new_group_name.strip():
        raise ValueError("Enter a name for the new influence group.")
    session._object_mode()
    pause(mesh)
    if action == 'NEW':
        group = mesh.vertex_groups.new(name=state.new_group_name.strip())
    if action == 'REMOVE':
        group.remove(selected)
    else:
        group.add(selected, state.mask_weight, 'REPLACE')
    state.mask_group = group.name
    state.use_mask = True
    mesh.vertex_groups.active_index = group.index
    mesh.data.update()
    context.scene.curvemorph_settings.target = mesh
    return group


def create_controls(context):
    mesh = resolve_target(context)
    if mesh is None:
        raise ValueError("Select the character mesh first.")
    session._object_mode()
    stored = require(mesh, corners=True)
    if mesh.curvemorph.token:
        raise ValueError("Controls already exist. Apply Setup to rebuild them.")
    settings = context.scene.curvemorph_settings
    session._check_neutral_keys(mesh)
    begin_edit(context, mesh, 'LOOP')
    return session.create_session(context, settings.control_count, settings.radius, settings.falloff,
                                  corner_vertices=list(stored["corners"]),
                                  falloff_type=settings.falloff_type, random_seed=settings.random_seed)


def apply_setup(context, mesh):
    session._object_mode()
    stored = require(mesh, corners=True)
    session.reconfigure(mesh, list(stored["loop"]), list(stored["corners"]), mesh.curvemorph.control_count)


def discard_changes(mesh):
    applied = mesh.get(session._STATE)
    if not applied:
        raise ValueError("There is no applied setup to restore.")
    session._object_mode()
    mesh[KEY] = {"loop": list(applied["loop"]), "corners": list(applied.get("corners", [])),
                 "topology": applied["topology"]}
