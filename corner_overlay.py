"""Non-selectable viewport guide for the stored loop while choosing corners.

Uses Blender's built-in polyline shader; no helper objects or mesh attributes.
API reference: https://docs.blender.org/api/current/gpu.html
"""
import bmesh
import bpy

from . import setup

_HANDLE = None
_SHADER = None


def loop_segments(context):
    """Read the current edit cage without touching its selection or geometry."""
    mesh = context.active_object
    if (mesh is None or mesh.type != 'MESH' or mesh.mode != 'EDIT'
            or mesh.curvemorph.setup_kind != 'CORNERS'):
        return []
    stored = setup.read(mesh)
    if not stored:
        return []
    bm = bmesh.from_edit_mesh(mesh.data)
    counts = stored['topology'].split(':')
    if len(bm.verts) != int(counts[0]) or len(bm.faces) != int(counts[1]):
        return []  # Stored indices are stale; never highlight unrelated vertices.
    bm.verts.ensure_lookup_table()
    loop = list(stored['loop'])
    if len(loop) < 4 or min(loop) < 0 or max(loop) >= len(bm.verts):
        return []
    edges = []
    for a, b in zip(loop, loop[1:] + loop[:1]):
        first, second = bm.verts[a], bm.verts[b]
        edge = next((e for e in first.link_edges if e.other_vert(first) == second), None)
        if edge is None:
            return []
        if edge.hide or first.hide or second.hide:
            continue
        start, end = mesh.matrix_world @ first.co, mesh.matrix_world @ second.co
        # Small gaps leave native vertex dots (including orange selection) clear.
        edges.extend((tuple(start.lerp(end, .06)), tuple(start.lerp(end, .94))))
    return edges


def draw_world_lines(positions):
    """Draw an in-front cyan guide, restoring GPU state for other overlays."""
    import gpu
    from gpu_extras.batch import batch_for_shader
    global _SHADER
    if not positions:
        return
    if _SHADER is None:
        _SHADER = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    batch = batch_for_shader(_SHADER, 'LINES', {'pos': positions})
    blend = gpu.state.blend_get()
    depth = gpu.state.depth_test_get()
    depth_mask = gpu.state.depth_mask_get()
    try:
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        gpu.state.depth_mask_set(False)
        _SHADER.bind()
        _SHADER.uniform_float('viewportSize', gpu.state.viewport_get()[2:])
        _SHADER.uniform_float('lineWidth', 3.0)
        _SHADER.uniform_float('color', (0.05, 0.85, 1.0, 0.95))
        batch.draw(_SHADER)
    finally:
        gpu.state.depth_mask_set(depth_mask)
        gpu.state.depth_test_set(depth)
        gpu.state.blend_set(blend)


def _draw():
    context = bpy.context
    if (context.area is None or context.area.type != 'VIEW_3D'
            or context.region_data is None or not context.space_data.overlay.show_overlays):
        return
    mesh = context.active_object
    if mesh is None or not mesh.visible_get(viewport=context.space_data):
        return
    try:
        positions = loop_segments(context)
    except (ReferenceError, KeyError, ValueError, IndexError):
        return  # Undo/file loading can invalidate the edit cage during a redraw.
    draw_world_lines(positions)


def redraw():
    # Addon enable/disable may run under Blender's restricted registration data.
    for screen in getattr(bpy.data, 'screens', ()):
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def register():
    global _HANDLE
    if _HANDLE is None and not bpy.app.background:
        _HANDLE = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_VIEW')
    redraw()


def unregister():
    global _HANDLE, _SHADER
    if _HANDLE is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_HANDLE, 'WINDOW')
        _HANDLE = None
    _SHADER = None
    redraw()
