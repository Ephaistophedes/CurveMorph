"""Public facial operators, native Edit Mode pose, and global Undo/Redo."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from test_blender_facial import *
import bmesh

addon.register()
mesh = grid()
bpy.ops.ed.undo_push(message='Face fixture')
bpy.ops.object.mode_set(mode='EDIT')
bm = bmesh.from_edit_mesh(mesh.data)
bm.verts.index_update()
for face in bm.faces:
    face.select_set(False)
for edge in bm.edges:
    edge.select_set(False)
for vertex in bm.verts:
    vertex.select_set(False)
for edge in bm.edges:
    if all(v.index in (18, 19, 20, 21) for v in edge.verts):
        edge.select_set(True)
bmesh.update_edit_mesh(mesh.data)
assert bpy.ops.facepose.add_edges() == {'FINISHED'}
bpy.ops.ed.undo_push(message='Added brow')
assert len(mesh.face_pose_curves) == 1
bpy.ops.ed.undo()
s._tick()
mesh = bpy.data.objects['Face']
assert len(mesh.face_pose_curves) == 0
bpy.ops.ed.redo()
s._tick()
mesh = bpy.data.objects['Face']
assert len(mesh.face_pose_curves) == 1
assert bpy.ops.facepose.action(action='EDIT') == {'FINISHED'}
bpy.ops.curve.select_all(action='SELECT')
bpy.ops.transform.translate(value=(0, 0, .2))
s._tick()
mesh = bpy.data.objects['Face']
entry = f.active(mesh)
delta = s._coords(f.preview(mesh, entry).data) - s._basis(mesh)
assert np.linalg.norm(delta) > .1, 'Native Edit Mode must preview live'
assert bpy.ops.facepose.action(action='MIRROR') == {'FINISHED'}
bpy.ops.ed.undo_push(message='Mirrored brow')
assert len(mesh.face_pose_curves) == 2
bpy.ops.ed.undo()
s._tick()
mesh = bpy.data.objects['Face']
assert len(mesh.face_pose_curves) == 1
bpy.ops.ed.redo()
s._tick()
mesh = bpy.data.objects['Face']
assert len(mesh.face_pose_curves) == 2
assert bpy.ops.facepose.action(action='SAVE') == {'FINISHED'}
bpy.ops.ed.undo_push(message='Saved combined pose')
mesh = bpy.data.objects['Face']
assert 'Face Pose' in mesh.data.shape_keys.key_blocks
bpy.ops.ed.undo()
s._tick()
mesh = bpy.data.objects['Face']
assert 'Face Pose' not in mesh.data.shape_keys.key_blocks
assert np.linalg.norm(s._coords(f.preview(mesh, f.active(mesh)).data)-s._basis(mesh)) > .1
bpy.ops.ed.redo()
s._tick()
mesh = bpy.data.objects['Face']
assert 'Face Pose' in mesh.data.shape_keys.key_blocks
assert bpy.ops.facepose.action(action='REMOVE') == {'FINISHED'}
bpy.ops.ed.undo_push(message='Removed one control')
assert len(mesh.face_pose_curves) == 1
bpy.ops.ed.undo()
s._tick()
mesh = bpy.data.objects['Face']
assert len(mesh.face_pose_curves) == 2
assert 'Face Pose' in mesh.data.shape_keys.key_blocks
print('FACIAL_UNDO: create, native pose preview, mirror, combined save, remove, Undo/Redo passed')
