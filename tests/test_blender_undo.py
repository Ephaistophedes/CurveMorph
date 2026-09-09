"""Exercise the real operators across curve Edit Mode undo and redo."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from test_blender_integration import *

addon.register()
try:
    mesh, n, _ = fixture("UndoCharacter")
    select_loop(mesh, n)
    addon.setup.capture_loop(bpy.context)
    addon.setup.begin_edit(bpy.context, mesh, 'CORNERS')
    bm = bmesh.from_edit_mesh(mesh.data)
    bm.verts.ensure_lookup_table()
    for i in (0, n // 2):
        bm.verts[i].select_set(True)
    bmesh.update_edit_mesh(mesh.data)
    addon.setup.capture_corners(bpy.context)
    bpy.ops.ed.undo_push(message="Selected mouth loop")
    assert bpy.ops.curvemorph.create() == {'FINISHED'}
    bpy.ops.ed.undo_push(message="Created controls")
    mesh = bpy.data.objects["UndoCharacter"]
    name = mesh.curvemorph.preview_name
    neutral = key_coordinates(mesh, name)
    bpy.ops.ed.undo()
    session._tick()
    mesh = bpy.data.objects["UndoCharacter"]
    assert not mesh.curvemorph.token, 'Undo Create must remove the session'
    assert mesh.data.shape_keys is None, 'Undo Create must remove its temporary keys'
    bpy.ops.ed.redo()
    session._tick()
    mesh = bpy.data.objects["UndoCharacter"]
    assert mesh.curvemorph.token and mesh.curvemorph.curve
    edit_one_control(mesh)
    session._tick()
    posed = key_coordinates(mesh, name)
    assert max_difference(posed, neutral) > 0.03
    bpy.ops.ed.undo_push(message="Posed controls")
    bpy.ops.ed.undo()
    session._tick()
    mesh = bpy.data.objects["UndoCharacter"]
    assert max_difference(key_coordinates(mesh, name), neutral) < 2e-6
    bpy.ops.ed.redo()
    session._tick()
    mesh = bpy.data.objects["UndoCharacter"]
    assert max_difference(key_coordinates(mesh, name), posed) < 2e-6
    bpy.context.scene.curvemorph_settings.shape_name = "UndoSmile"
    assert bpy.ops.curvemorph.save() == {'FINISHED'}
    bpy.ops.ed.undo_push(message="Saved expression")
    mesh = bpy.data.objects["UndoCharacter"]
    assert "UndoSmile" in mesh.data.shape_keys.key_blocks
    assert max_difference(key_coordinates(mesh, name), neutral) < 2e-6
    bpy.ops.ed.undo()
    session._tick()
    mesh = bpy.data.objects["UndoCharacter"]
    assert "UndoSmile" not in mesh.data.shape_keys.key_blocks
    assert max_difference(key_coordinates(mesh, name), posed) < 2e-6
    bpy.ops.ed.redo()
    session._tick()
    mesh = bpy.data.objects["UndoCharacter"]
    assert "UndoSmile" in mesh.data.shape_keys.key_blocks
    assert bpy.ops.curvemorph.finish() == {'FINISHED'}
    bpy.ops.ed.undo_push(message="Finished session")
    assert not bpy.data.objects["UndoCharacter"].curvemorph.token
    bpy.ops.ed.undo()
    session._tick()
    mesh = bpy.data.objects["UndoCharacter"]
    assert mesh.curvemorph.token and mesh.curvemorph.curve
    assert "UndoSmile" in mesh.data.shape_keys.key_blocks
    bpy.ops.ed.undo_push(message="Before rebuilding")
    assert bpy.ops.curvemorph.rebuild(control_count=12) == {'FINISHED'}
    bpy.ops.ed.undo_push(message="Rebuilt controls")
    assert mesh.curvemorph.control_count == 12
    bpy.ops.ed.undo()
    session._tick()
    mesh = bpy.data.objects["UndoCharacter"]
    assert mesh.curvemorph.control_count == 8
    assert len(mesh.curvemorph.curve.data.splines[0].bezier_points) == 8
    bpy.ops.ed.redo()
    session._tick()
    mesh = bpy.data.objects["UndoCharacter"]
    assert mesh.curvemorph.control_count == 12
    # Setup edits cross mesh Edit Mode and must restore their persistent data.
    old_corners = list(addon.setup.read(mesh)['corners'])
    addon.setup.begin_edit(bpy.context, mesh, 'CORNERS')
    bm = bmesh.from_edit_mesh(mesh.data)
    bm.verts.ensure_lookup_table()
    for v in bm.verts:
        v.select_set(False)
    for i in (1, n // 2 + 1):
        bm.verts[i].select_set(True)
    bmesh.update_edit_mesh(mesh.data)
    bpy.ops.ed.undo_push(message="Before storing corners")
    assert bpy.ops.curvemorph.setup_capture(kind='CORNERS') == {'FINISHED'}
    bpy.ops.ed.undo_push(message="Stored new corners")
    assert addon.setup.pending(mesh)
    bpy.ops.ed.undo()
    mesh = bpy.data.objects["UndoCharacter"]
    assert list(addon.setup.read(mesh)['corners']) == old_corners
    bpy.ops.ed.redo()
    mesh = bpy.data.objects["UndoCharacter"]
    assert list(addon.setup.read(mesh)['corners']) == [1, n // 2 + 1]
    assert bpy.ops.curvemorph.setup_apply() == {'FINISHED'}
    bpy.ops.ed.undo_push(message="Applied new corners")
    assert not addon.setup.pending(mesh)
    bpy.ops.ed.undo()
    mesh = bpy.data.objects["UndoCharacter"]
    assert addon.setup.pending(mesh) and mesh.curvemorph.setup_editing
    bpy.ops.ed.redo()
    mesh = bpy.data.objects["UndoCharacter"]
    assert not addon.setup.pending(mesh) and not mesh.curvemorph.setup_editing
    addon.setup.begin_edit(bpy.context, mesh, 'MASK')
    bm = bmesh.from_edit_mesh(mesh.data)
    bm.verts.ensure_lookup_table()
    bm.verts[0].select_set(True)
    bmesh.update_edit_mesh(mesh.data)
    bpy.ops.ed.undo_push(message="Before creating mask")
    assert bpy.ops.curvemorph.mask_selection(action='NEW') == {'FINISHED'}
    bpy.ops.ed.undo_push(message="Created mask")
    group_name = mesh.curvemorph.mask_group
    assert mesh.vertex_groups.get(group_name)
    bpy.ops.ed.undo()
    mesh = bpy.data.objects["UndoCharacter"]
    assert mesh.vertex_groups.get(group_name) is None
    bpy.ops.ed.redo()
    mesh = bpy.data.objects["UndoCharacter"]
    assert mesh.vertex_groups.get(group_name) and mesh.curvemorph.use_mask
    print("CURVEMORPH_UNDO: create, pose, save, finish, rebuild, corners, apply setup and mask passed.")
finally:
    addon.unregister()
