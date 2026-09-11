"""Curve-only fit editing, rest rebinding, cancellation, persistence and Undo."""
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parent))
from test_blender_integration import *
from test_blender_setup import stored_setup
import numpy as np


class CurveFitTests(unittest.TestCase):
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
        self.mesh, self.n, _ = fixture('FitCharacter')
        stored_setup(self.mesh, self.n)
        bpy.context.scene.curvemorph_settings.control_count = 8
        addon.setup.create_controls(bpy.context)
        session._object_mode()
        self.neutral = session._basis(self.mesh).copy()

    def assert_neutral(self):
        np.testing.assert_array_equal(session._coords(session._preview(self.mesh).data), self.neutral)
        np.testing.assert_array_equal(session._basis(self.mesh), self.neutral)

    def adjust(self):
        edit_one_control(self.mesh, (.1, -.08, .15))  # Native Edit Mode G transform.
        session._object_mode()
        curve = self.mesh.curvemorph.curve
        point = curve.data.splines[0].bezier_points[1]
        point.handle_left_type = point.handle_right_type = 'FREE'
        point.handle_left.z += .06
        point.handle_right.y += .08
        point.tilt = .3
        curve.data.update_tag()
        session._tick()
        self.assert_neutral()

    def test_apply_freezes_mesh_then_pose_and_reset_use_custom_fit(self):
        mesh = self.mesh
        edit_one_control(mesh)
        saved = session.save_shape_key(mesh, 'KeepMe', reset_after=True)
        saved_before = coordinates(saved.data)
        group = mesh.vertex_groups.new(name='KeepWeights')
        group.add([0, 2], .3, 'REPLACE')
        loop_before = list(mesh[session._STATE]['loop'])
        self.assertEqual(bpy.ops.curvemorph.fit_begin(), {'FINISHED'})
        self.adjust()
        curve = mesh.curvemorph.curve
        curve.data.splines[0].tilt_interpolation = 'EASE'
        fitted, types = session._curve_arrays(curve).copy(), session._handle_types(curve)
        tilts = session._curve_tilts(curve).copy()
        self.assertEqual(bpy.ops.curvemorph.fit_end(), {'FINISHED'})
        self.assert_neutral()
        self.assertFalse(mesh.curvemorph.fit_editing)
        self.assertIsNone(mesh.curvemorph.fit_backup)
        edit_one_control(mesh)
        self.assertGreater(np.max(abs(session._coords(session._preview(mesh).data) - self.neutral)), .1)
        session.reset_pose(mesh)
        self.assert_neutral()
        np.testing.assert_allclose(session._curve_arrays(curve), fitted, atol=1e-7)
        np.testing.assert_array_equal(session._curve_tilts(curve), tilts)
        self.assertEqual(session._handle_types(curve), types)
        self.assertEqual(curve.data.splines[0].tilt_interpolation, 'EASE')
        self.assertEqual(coordinates(saved.data), saved_before)
        self.assertAlmostEqual(group.weight(0), .3, places=6)
        self.assertEqual(list(mesh[session._STATE]['loop']), loop_before)
        session.rebuild_controls(mesh, 9)
        self.assert_neutral()
        self.assertNotIn('rest_tilts', mesh[session._STATE])
        self.assertNotIn('rest_handle_types', mesh[session._STATE])

    def test_cancel_recovers_accidental_point_deletion_and_transforms(self):
        mesh, curve = self.mesh, self.mesh.curvemorph.curve
        before, types = session._curve_arrays(curve).copy(), session._handle_types(curve)
        session.begin_curve_fit(bpy.context, mesh)
        self.adjust()
        session._set_curve(curve, before[:5, 0])
        curve.location = (.2, .3, .4)
        # Cancel must restore the original topology even though five points are now valid.
        self.assertTrue(mesh.curvemorph.fit_editing)
        self.assertEqual(bpy.ops.curvemorph.fit_end(cancel=True), {'FINISHED'})
        self.assert_neutral()
        np.testing.assert_array_equal(session._curve_arrays(curve), before)
        self.assertEqual(session._handle_types(curve), types)
        np.testing.assert_allclose(np.asarray(curve.matrix_basis), np.eye(4), atol=1e-7)

    def test_add_remove_points_apply_updates_count_and_reset(self):
        mesh, curve = self.mesh, self.mesh.curvemorph.curve
        before = session._curve_arrays(curve).copy()
        reference = np.asarray(mesh[session._STATE]['rest_samples']).reshape((-1, 3))
        session.begin_curve_fit(bpy.context, mesh)
        bpy.ops.curve.select_all(action='DESELECT')
        for point in curve.data.splines[0].bezier_points[:2]:
            point.select_control_point = point.select_left_handle = point.select_right_handle = True
        bpy.ops.curve.subdivide(number_cuts=1)
        session.apply_curve_fit(mesh)
        self.assertEqual(mesh.curvemorph.control_count, len(before) + 1)
        np.testing.assert_allclose(np.asarray(mesh[session._STATE]['rest_samples']).reshape((-1, 3)), reference, atol=2e-5)
        self.assert_neutral()
        session.begin_curve_fit(bpy.context, mesh)
        bpy.ops.curve.select_all(action='DESELECT')
        curve.data.splines[0].bezier_points[0].select_control_point = True
        bpy.ops.curve.delete(type='VERT')
        session.apply_curve_fit(mesh)
        self.assertEqual(mesh.curvemorph.control_count, len(before))
        self.assert_neutral()
        fitted = session._curve_arrays(curve).copy()
        edit_one_control(mesh)
        self.assertGreater(np.linalg.norm(session._coords(session._preview(mesh).data)-self.neutral), .01)
        session.save_shape_key(mesh, 'Refitted Mouth', reset_after=True)
        np.testing.assert_allclose(session._curve_arrays(curve), fitted, atol=1e-7)
        self.assert_neutral()

    def test_transformed_fit_and_mixed_handle_types_reset_exactly(self):
        mesh, curve = self.mesh, self.mesh.curvemorph.curve
        mesh.location = (2, -3, 4)
        mesh.rotation_euler = (.2, -.4, .7)
        mesh.scale = (2, .8, 1.3)
        bpy.context.view_layer.update()
        session.begin_curve_fit(bpy.context, mesh)
        self.adjust()
        points = curve.data.splines[0].bezier_points
        points[2].handle_left_type = points[2].handle_right_type = 'VECTOR'
        points[3].handle_left_type = points[3].handle_right_type = 'ALIGNED'
        curve.data.update_tag()
        bpy.context.view_layer.update()
        types = session._handle_types(curve)
        session.apply_curve_fit(mesh)
        rest = session._curve_arrays(curve).copy()
        edit_one_control(mesh)
        session.reset_pose(mesh)
        self.assert_neutral()
        np.testing.assert_allclose(session._curve_arrays(curve), rest, atol=1e-7)
        self.assertEqual(session._handle_types(curve), types)
        session.begin_curve_fit(bpy.context, mesh)
        session._object_mode()
        curve.location = (.2, -.15, .1)
        curve.scale = (1.2, .8, 1.1)
        bpy.context.view_layer.update()
        arrays = session._curve_arrays(curve)
        relative = curve.matrix_parent_inverse @ curve.matrix_basis
        expected = session._transform(arrays.reshape(-1, 3), relative).reshape(arrays.shape)
        session._tick()
        self.assert_neutral()
        session.apply_curve_fit(mesh)
        self.assert_neutral()
        np.testing.assert_allclose(session._curve_arrays(curve), expected, atol=1e-6)
        session.reset_pose(mesh)
        self.assert_neutral()

    def test_tilt_after_fit_uses_the_new_rest_tilt(self):
        mesh = self.mesh
        session.begin_curve_fit(bpy.context, mesh)
        self.adjust()
        session.apply_curve_fit(mesh)
        curve = mesh.curvemorph.curve
        point = curve.data.splines[0].bezier_points[1]
        point.tilt += .4
        curve.data.update_tag()
        session.update_session(mesh, force=True)
        posed = session._coords(session._preview(mesh).data)
        self.assertGreater(np.max(abs(posed - self.neutral)), .01)
        np.testing.assert_allclose(posed[:self.n], self.neutral[:self.n], atol=1e-7)
        session.save_shape_key(mesh, 'FitTilt', reset_after=True)
        self.assert_neutral()

    def test_guards_preserve_active_pose_and_paused_fit(self):
        mesh = self.mesh
        edit_one_control(mesh)
        posed = session._coords(session._preview(mesh).data).copy()
        before = session._curve_arrays(mesh.curvemorph.curve).copy()
        with self.assertRaisesRegex(ValueError, 'Reset Pose'):
            session.begin_curve_fit(bpy.context, mesh)
        np.testing.assert_array_equal(session._coords(session._preview(mesh).data), posed)
        np.testing.assert_array_equal(session._curve_arrays(mesh.curvemorph.curve), before)
        self.assertFalse(mesh.curvemorph.fit_editing)
        session.reset_pose(mesh)
        session.begin_curve_fit(bpy.context, mesh)
        for action in (lambda: session.reset_pose(mesh), lambda: session.rebuild_controls(mesh, 9),
                       lambda: session.save_shape_key(mesh, 'ShouldNotExist'),
                       lambda: addon.setup.begin_edit(bpy.context, mesh, 'LOOP')):
            with self.assertRaisesRegex(ValueError, 'Apply Fit or Cancel'):
                action()
        self.assert_neutral()
        session.cancel_curve_fit(mesh)

    def test_fit_and_cancel_backup_survive_save_reload(self):
        mesh = self.mesh
        before = session._curve_arrays(mesh.curvemorph.curve).copy()
        session.begin_curve_fit(bpy.context, mesh)
        self.adjust()
        with tempfile.TemporaryDirectory(prefix='mouth_fit_') as directory:
            path = str(Path(directory) / 'fit.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
            bpy.ops.wm.open_mainfile(filepath=path)
            self.mesh = mesh = bpy.data.objects['FitCharacter']
            self.assertTrue(mesh.curvemorph.fit_editing)
            session._tick()
            self.assert_neutral()
            session.cancel_curve_fit(mesh)
            np.testing.assert_array_equal(session._curve_arrays(mesh.curvemorph.curve), before)
            session.begin_curve_fit(bpy.context, mesh)
            self.adjust()
            session.apply_curve_fit(mesh)
            fitted = session._curve_arrays(mesh.curvemorph.curve).copy()
            bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
            bpy.ops.wm.open_mainfile(filepath=path)
            self.mesh = mesh = bpy.data.objects['FitCharacter']
            edit_one_control(mesh)
            session.reset_pose(mesh)
            self.assert_neutral()
            np.testing.assert_allclose(session._curve_arrays(mesh.curvemorph.curve), fitted, atol=1e-7)

    def test_failed_apply_keeps_edits_and_cancel_available(self):
        mesh = self.mesh
        session.begin_curve_fit(bpy.context, mesh)
        self.adjust()
        before = session._curve_arrays(mesh.curvemorph.curve).copy()
        raw = mesh[session._STATE].to_dict()
        with patch.object(session, 'update_session', side_effect=RuntimeError('Injected failure')):
            with self.assertRaisesRegex(RuntimeError, 'Injected failure'):
                session.apply_curve_fit(mesh)
        self.assertEqual(mesh[session._STATE].to_dict(), raw)
        self.assertTrue(mesh.curvemorph.fit_editing)
        self.assertIsNotNone(mesh.curvemorph.fit_backup)
        np.testing.assert_array_equal(session._curve_arrays(mesh.curvemorph.curve), before)
        self.assert_neutral()
        session.cancel_curve_fit(mesh)

    def test_operator_apply_undo_redo_restores_fit_mode_and_rest(self):
        before = session._curve_arrays(self.mesh.curvemorph.curve).copy()
        bpy.ops.ed.undo_push(message='Before fit')
        bpy.ops.curvemorph.fit_begin()
        bpy.ops.ed.undo_push(message='Started fit')
        bpy.ops.ed.undo()
        self.mesh = bpy.data.objects['FitCharacter']
        self.assertFalse(self.mesh.curvemorph.fit_editing)
        bpy.ops.ed.redo()
        self.mesh = bpy.data.objects['FitCharacter']
        self.assertTrue(self.mesh.curvemorph.fit_editing)
        self.adjust()
        session.edit_controls(bpy.context, self.mesh)
        bpy.ops.curve.select_all(action='SELECT')
        bpy.ops.curve.subdivide(number_cuts=1)
        session._object_mode()
        bpy.ops.ed.undo_push(message='Edited fit')
        self.assertEqual(bpy.ops.curvemorph.fit_end(), {'FINISHED'})
        bpy.ops.ed.undo_push(message='Applied fit')
        bpy.ops.ed.undo()
        self.mesh = bpy.data.objects['FitCharacter']
        self.assertTrue(self.mesh.curvemorph.fit_editing)
        self.assertEqual(self.mesh.curvemorph.control_count, len(before))
        self.assertIsNotNone(self.mesh.curvemorph.fit_backup)
        session._tick()
        self.assert_neutral()
        np.testing.assert_array_equal(np.asarray(self.mesh[session._STATE]['rest_curve']).reshape(before.shape), before)
        bpy.ops.ed.redo()
        self.mesh = bpy.data.objects['FitCharacter']
        self.assertFalse(self.mesh.curvemorph.fit_editing)
        self.assertEqual(self.mesh.curvemorph.control_count, len(before)*2)
        session._tick()
        self.assert_neutral()


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CurveFitTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
