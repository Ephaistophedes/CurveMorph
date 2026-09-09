"""Symmetrize controls and handles, preserving saved keys and supporting Undo."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from test_blender_integration import *
from test_blender_setup import stored_setup
import numpy as np


class SymmetryTests(unittest.TestCase):
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

    def create(self, count=8):
        mesh, n, _ = fixture('SymmetryCharacter')
        stored_setup(mesh, n)
        bpy.context.scene.curvemorph_settings.control_count = count
        addon.setup.create_controls(bpy.context)
        return mesh

    def test_both_directions_handles_crossing_center_and_odd_counts(self):
        for count in (4, 5, 8, 9, 16):
            for direction in ('POSITIVE', 'NEGATIVE'):
                with self.subTest(count=count, direction=direction):
                    mesh = self.create(count)
                    curve = mesh.curvemorph.curve
                    rest = session._curve_arrays(curve).copy()
                    points = curve.data.splines[0].bezier_points
                    sign = 1 if direction == 'POSITIVE' else -1
                    source = int(np.argmax(sign * rest[:, 0, 0]))
                    for p in points:
                        p.handle_left_type = p.handle_right_type = 'FREE'
                    p = points[source]
                    p.co.x = -p.co.x  # Posed X is not used to choose the source.
                    p.co.z += .3
                    p.handle_left.z += .17
                    p.handle_right.y += .23
                    before = session._curve_arrays(curve).copy()
                    session.symmetrize_pose(mesh, direction)
                    after = session._curve_arrays(curve)
                    np.testing.assert_allclose(after[source], before[source], atol=1e-6)
                    mirrored = before[source, [0, 2, 1]] * [-1, 1, 1]
                    self.assertLess(min(np.max(abs(a - mirrored)) for a in after), 1e-6)
                    session.symmetrize_pose(mesh, direction)
                    np.testing.assert_allclose(session._curve_arrays(curve), after, atol=1e-6)
                    session.finish_session(mesh)

    def test_transforms_mask_and_saved_keys_preserved(self):
        mesh = self.create()
        curve = mesh.curvemorph.curve
        mesh.location = (5, 3, -2)
        mesh.rotation_euler = (.3, .7, 1.2)
        mesh.scale = (2, .8, 1.3)
        curve.location = (.03, .04, -.07)
        bpy.context.view_layer.update()
        group = mesh.vertex_groups.new(name='KeepMask')
        group.add([0], .3, 'REPLACE')
        mesh.curvemorph.mask_group = group.name
        mesh.curvemorph.use_mask = True
        edit_one_control(mesh)
        saved = session.save_shape_key(mesh, 'KeepExpression', reset_after=False)
        saved_before = coordinates(saved.data)
        before = session._curve_arrays(curve)
        relative = mesh.matrix_world.inverted() @ curve.matrix_world
        local = session._transform(before.reshape((-1, 3)), relative).reshape(before.shape)
        rest = np.array(mesh[session._STATE]['rest_curve']).reshape(before.shape)
        expected = addon.geometry.symmetrize_bezier(rest, local, 'NEGATIVE')
        session.symmetrize_pose(mesh, 'NEGATIVE')
        after = session._curve_arrays(curve)
        actual = session._transform(after.reshape((-1, 3)), relative).reshape(after.shape)
        np.testing.assert_allclose(actual, expected, atol=1e-6)
        self.assertEqual(coordinates(saved.data), saved_before)
        self.assertAlmostEqual(group.weight(0), .3, places=6)
        self.assertEqual(mesh.curvemorph.mask_group, 'KeepMask')

    def test_paused_setup_rejected(self):
        mesh = self.create()
        addon.setup.begin_edit(bpy.context, mesh, 'CORNERS')
        before = session._curve_arrays(mesh.curvemorph.curve).copy()
        with self.assertRaisesRegex(ValueError, 'Resume'):
            session.symmetrize_pose(mesh, 'POSITIVE')
        np.testing.assert_array_equal(session._curve_arrays(mesh.curvemorph.curve), before)

    def test_real_operator_edit_mode_undo_redo(self):
        mesh = self.create()
        edit_one_control(mesh)
        before = session._curve_arrays(mesh.curvemorph.curve).copy()
        bpy.ops.ed.undo_push(message='Before symmetry')
        self.assertEqual(bpy.ops.curvemorph.symmetrize(direction='POSITIVE'), {'FINISHED'})
        after = session._curve_arrays(mesh.curvemorph.curve).copy()
        self.assertGreater(np.max(abs(after - before)), .01)
        bpy.ops.ed.undo_push(message='Symmetrized')
        bpy.ops.ed.undo()
        mesh = bpy.data.objects['SymmetryCharacter']
        session._tick()
        np.testing.assert_allclose(session._curve_arrays(mesh.curvemorph.curve), before, atol=1e-6)
        bpy.ops.ed.redo()
        mesh = bpy.data.objects['SymmetryCharacter']
        session._tick()
        np.testing.assert_allclose(session._curve_arrays(mesh.curvemorph.curve), after, atol=1e-6)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SymmetryTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
