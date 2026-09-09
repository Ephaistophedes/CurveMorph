"""Native Ctrl+T / Alt+T tilt, deformation, masks, symmetry and persistence."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from test_blender_integration import *
from test_blender_setup import stored_setup
import numpy as np


def tilt_one(mesh, angle=math.pi/4):
    curve = mesh.curvemorph.curve
    activate(curve)
    for i, point in enumerate(curve.data.splines[0].bezier_points):
        point.select_control_point = point.select_left_handle = point.select_right_handle = i == 0
    bpy.ops.object.mode_set(mode='EDIT')
    assert bpy.ops.transform.tilt(value=angle) == {'FINISHED'}
    session.update_session(mesh)  # No force: native Edit Mode changes must be detected.


class TiltTests(unittest.TestCase):
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
        self.mesh, self.n, self.island = fixture('TiltCharacter')
        stored_setup(self.mesh, self.n)
        addon.setup.create_controls(bpy.context)
        self.neutral = coordinates(self.mesh.data.vertices)

    def preview(self):
        return coordinates(session._preview(self.mesh).data)

    def test_native_tilt_changes_neighbors_not_loop_and_clear_restores(self):
        mesh = self.mesh
        before_curve = session._curve_arrays(mesh.curvemorph.curve).copy()
        tilt_one(mesh)
        self.assertEqual(mesh.curvemorph.curve.mode, 'EDIT')
        np.testing.assert_array_equal(session._curve_arrays(mesh.curvemorph.curve), before_curve)
        self.assertAlmostEqual(session._curve_tilts(mesh.curvemorph.curve)[0], math.pi/4, places=6)
        self.assertGreater(max_difference(self.preview(), self.neutral), .01)
        self.assertLess(self.preview()[self.n][2], -.01)  # +Y tangent rolls outward +X toward -Z.
        self.assertLess(max_difference(self.preview(), self.neutral, range(self.n)), 1e-6)
        self.assertEqual(max_difference(self.preview(), self.neutral, self.island), 0)
        self.assertEqual(bpy.ops.curve.tilt_clear(), {'FINISHED'})
        session.update_session(mesh)
        self.assertLess(max_difference(self.preview(), self.neutral), 1e-6)

    def test_tilt_only_key_save_reset_rebuild(self):
        mesh = self.mesh
        tilt_one(mesh)
        rolled = self.preview()
        key = session.save_shape_key(mesh, 'LipRoll')
        self.assertLess(max_difference(coordinates(key.data), rolled), 1e-6)
        self.assertEqual(key.value, 0)
        self.assertTrue(np.all(session._curve_tilts(mesh.curvemorph.curve) == 0))
        self.assertLess(max_difference(self.preview(), self.neutral), 1e-6)
        tilt_one(mesh)
        session.rebuild_controls(mesh, 12)
        self.assertTrue(np.all(session._curve_tilts(mesh.curvemorph.curve) == 0))
        self.assertLess(max_difference(coordinates(key.data), rolled), 1e-6)

    def test_radius_and_mask_apply_to_tilt_once(self):
        mesh = self.mesh
        tilt_one(mesh)
        full = self.preview()
        session._object_mode()
        group = mesh.vertex_groups.new(name='TiltMask')
        group.add([self.n], .25, 'REPLACE')
        mesh.curvemorph.use_mask = True
        mesh.curvemorph.mask_group = group.name
        session.update_session(mesh)
        masked = self.preview()
        expected = np.array(self.neutral[self.n]) + .25*(np.array(full[self.n])-self.neutral[self.n])
        np.testing.assert_allclose(masked[self.n], expected, atol=1e-6)
        self.assertEqual(max_difference(masked, self.neutral, [i for i in range(len(masked)) if i != self.n]), 0)
        mesh.curvemorph.use_mask = False
        mesh.curvemorph.radius = 0
        session.update_session(mesh)
        self.assertLess(max_difference(self.preview(), self.neutral), 1e-6)
        mesh.curvemorph.radius = .8
        session.update_session(mesh)
        self.assertGreater(max_difference(self.preview(), self.neutral), .01)

    def test_interpolation_changes_invalidate_preview(self):
        mesh = self.mesh
        tilt_one(mesh)
        session._object_mode()
        spline = mesh.curvemorph.curve.data.splines[0]
        linear = self.preview()
        for mode in ('EASE', 'CARDINAL', 'BSPLINE'):
            spline.tilt_interpolation = mode
            session.update_session(mesh)
            self.assertTrue(np.isfinite(self.preview()).all())
            self.assertGreater(max_difference(self.preview(), linear), 1e-5)

    def test_symmetry_copies_tilt_with_correct_sign(self):
        mesh = self.mesh
        tilt_one(mesh)
        session.symmetrize_pose(mesh, 'POSITIVE')
        points = mesh.curvemorph.curve.data.splines[0].bezier_points
        self.assertAlmostEqual(points[4].tilt, points[0].tilt, places=6)
        posed = np.array(self.preview())
        for ring in range(5):
            indices = ring*self.n + np.arange(self.n)
            mirrored = ring*self.n + (self.n//2 - np.arange(self.n)) % self.n
            np.testing.assert_allclose(posed[indices]*[-1, 1, 1], posed[mirrored], atol=3e-6)

    def test_real_tilt_undo_redo(self):
        mesh = self.mesh
        session.edit_controls(bpy.context, mesh)
        bpy.ops.ed.undo_push(message='Before tilt')
        tilt_one(mesh)
        rolled = self.preview()
        bpy.ops.ed.undo_push(message='Tilted lip')
        bpy.ops.ed.undo()
        self.mesh = mesh = bpy.data.objects['TiltCharacter']
        session._tick()
        self.assertLess(max_difference(self.preview(), self.neutral), 1e-6)
        bpy.ops.ed.redo()
        self.mesh = mesh = bpy.data.objects['TiltCharacter']
        session._tick()
        self.assertLess(max_difference(self.preview(), rolled), 1e-6)

    def test_reload_preserves_tilt_on_transformed_mesh(self):
        mesh = self.mesh
        mesh.location = (3, -2, 5)
        mesh.rotation_euler = (.3, .7, .2)
        mesh.scale = (-1.5, .8, 2)
        bpy.context.view_layer.update()
        tilt_one(mesh)
        rolled = self.preview()
        self.assertGreater(max_difference(rolled, self.neutral), .01)
        session._object_mode()
        with tempfile.TemporaryDirectory(prefix='mouth_tilt_') as directory:
            path = str(Path(directory) / 'tilt.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
            bpy.ops.wm.open_mainfile(filepath=path)
            self.mesh = mesh = bpy.data.objects['TiltCharacter']
            session.update_session(mesh)
            self.assertAlmostEqual(session._curve_tilts(mesh.curvemorph.curve)[0], math.pi/4, places=6)
            self.assertLess(max_difference(self.preview(), rolled), 1e-6)

    def test_zero_tilt_after_movement_matches_existing_pose(self):
        mesh = self.mesh
        edit_one_control(mesh)
        translated = self.preview()
        tilt_one(mesh)
        self.assertGreater(max_difference(self.preview(), translated), .01)
        bpy.ops.curve.tilt_clear()
        session.update_session(mesh)
        self.assertLess(max_difference(self.preview(), translated), 1e-6)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(TiltTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
