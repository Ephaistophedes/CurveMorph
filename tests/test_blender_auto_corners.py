"""Local-X auto corner selection, confirmation, pose preservation and Undo."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from test_blender_integration import *
from test_blender_setup import stored_setup
import numpy as np


def selected(mesh):
    return sorted(v.index for v in bmesh.from_edit_mesh(mesh.data).verts if v.select)


class AutoCornerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        addon.register()

    @classmethod
    def tearDownClass(cls):
        session._object_mode()
        addon.unregister()

    def setUp(self):
        session._object_mode()
        session._CACHE.clear()
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)

    def test_auto_uses_stored_loop_local_axis_and_requires_confirmation(self):
        mesh, n, _ = fixture()
        select_loop(mesh, n)
        addon.setup.capture_loop(bpy.context)
        mesh.location = (8, -3, 5)
        mesh.rotation_euler = (0, 0, math.pi/2)
        mesh.scale = (-2, .6, 1.3)
        bpy.context.view_layer.update()
        self.assertEqual(bpy.ops.curvemorph.auto_corners(), {'FINISHED'})
        self.assertEqual(selected(mesh), [0, n//2])
        self.assertEqual(list(addon.setup.read(mesh)['corners']), [])
        self.assertEqual(len(addon.corner_overlay.loop_segments(bpy.context)), 2*n)
        self.assertEqual(bpy.ops.curvemorph.setup_capture(kind='CORNERS'), {'FINISHED'})
        self.assertEqual(list(addon.setup.read(mesh)['corners']), [0, n//2])

    def test_auto_keeps_current_control_pose_saved_keys_and_applied_setup(self):
        mesh, n, _ = fixture()
        stored_setup(mesh, n, (3, 18))
        addon.setup.create_controls(bpy.context)
        edit_one_control(mesh)
        saved = session.save_shape_key(mesh, 'KeepPose', reset_after=False)
        before_key = coordinates(saved.data)
        before_curve = session._curve_arrays(mesh.curvemorph.curve).copy()
        before_setup = mesh[session._STATE].to_dict()
        session.edit_controls(bpy.context, mesh)
        self.assertEqual(bpy.ops.curvemorph.auto_corners(), {'FINISHED'})
        self.assertEqual(selected(mesh), [0, n//2])
        self.assertTrue(mesh.curvemorph.setup_editing)
        self.assertEqual(session._preview(mesh).value, 0)
        self.assertEqual(list(addon.setup.read(mesh)['corners']), [3, 18])
        self.assertEqual(mesh[session._STATE].to_dict(), before_setup)
        np.testing.assert_array_equal(session._curve_arrays(mesh.curvemorph.curve), before_curve)
        self.assertEqual(coordinates(saved.data), before_key)

    def test_tied_extremes_are_deterministic_and_zero_width_is_rejected(self):
        mesh, n, _ = fixture()
        mesh.data.vertices[1].co.x = mesh.data.vertices[0].co.x
        mesh.data.vertices[n//2+1].co.x = mesh.data.vertices[n//2].co.x
        select_loop(mesh, n)
        addon.setup.capture_loop(bpy.context)
        addon.setup.select_auto_corners(bpy.context)
        self.assertEqual(selected(mesh), [0, n//2])
        addon.setup.capture_corners(bpy.context)
        before = addon.setup.read(mesh).to_dict()
        for v in mesh.data.vertices:
            v.co.x = 0
        mesh.data.update()
        with self.assertRaisesRegex(ValueError, 'local-X width'):
            addon.setup.select_auto_corners(bpy.context)
        self.assertEqual(addon.setup.read(mesh).to_dict(), before)

    def test_auto_selection_undo_redo(self):
        mesh, n, _ = fixture('UndoAuto')
        stored_setup(mesh, n, (3, 18))
        addon.setup.begin_edit(bpy.context, mesh, 'CORNERS')
        self.assertEqual(selected(mesh), [3, 18])
        bpy.ops.ed.undo_push(message='Before auto selection')
        bpy.ops.curvemorph.auto_corners()
        bpy.ops.ed.undo_push(message='Auto selected corners')
        self.assertEqual(selected(mesh), [0, n//2])
        bpy.ops.ed.undo()
        mesh = bpy.data.objects['UndoAuto']
        self.assertEqual(selected(mesh), [3, 18])
        bpy.ops.ed.redo()
        mesh = bpy.data.objects['UndoAuto']
        self.assertEqual(selected(mesh), [0, n//2])


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(AutoCornerTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
