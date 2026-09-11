"""Corner fidelity and neutral/rest persistence in an isolated Blender process."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from test_blender_integration import *
from test_blender_setup import stored_setup
import numpy as np


class CornerFitTests(unittest.TestCase):
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

    def create(self, count=8, transformed=False):
        mesh, n, _ = fixture('SharpMouth', angular_count=64)
        for i, vertex in enumerate(mesh.data.vertices[:n]):
            x = math.cos(2 * math.pi * i / n)
            height = 1 - x*x
            vertex.co = (x, (.32 if i < n//2 else -.32) * height, .07 * height)
        if transformed:
            mesh.location = (2, -3, 1)
            mesh.rotation_euler = (.4, -.3, .8)
            mesh.scale = (2, .7, 1.4)
        bpy.context.view_layer.update()
        stored_setup(mesh, n)
        bpy.context.scene.curvemorph_settings.control_count = count
        addon.setup.create_controls(bpy.context)
        session._object_mode()
        return mesh, n

    def test_corner_curve_is_closer_than_automatic_handles(self):
        for count in (4, 5, 8, 9, 16):
            with self.subTest(count=count):
                mesh, n = self.create(count, transformed=True)
                curve = mesh.curvemorph.curve
                fitted = session._curve_arrays(curve)
                raw = mesh[session._STATE]
                params = np.asarray(raw['parameters'])
                loop = session._basis(mesh)[list(raw['loop'])]
                session._set_curve(curve, fitted[:, 0])
                automatic = session._curve_arrays(curve)
                near = [1, 2, n//2-2, n//2-1, n//2+1, n//2+2, n-2, n-1]
                def error(arrays):
                    samples = addon.geometry.evaluate_bezier(*arrays.transpose(1, 0, 2), params)
                    return np.linalg.norm(samples[near] - loop[near], axis=1).mean()
                before, after = error(automatic), error(fitted)
                print(f'CORNER_ERROR count={count}: auto={before:.6f}, fitted={after:.6f}')
                self.assertLess(after, before * .4)
                # Rebuild also replaces the binding's rest, keeping the mesh neutral.
                session.rebuild_controls(mesh, count)
                np.testing.assert_allclose(session._curve_arrays(curve), fitted, atol=1e-6)
                np.testing.assert_allclose(session._coords(session._preview(mesh).data), session._basis(mesh), atol=1e-6)
                session.finish_session(mesh)

    def test_reset_save_reload_and_rebuild_preserve_neutral_and_saved_pose(self):
        mesh, n = self.create()
        rest = session._curve_arrays(mesh.curvemorph.curve).copy()
        edit_one_control(mesh)
        saved = session.save_shape_key(mesh, 'KeepCornerPose', reset_after=True)
        saved_before = coordinates(saved.data)
        np.testing.assert_allclose(session._curve_arrays(mesh.curvemorph.curve), rest, atol=1e-6)
        np.testing.assert_allclose(session._coords(session._preview(mesh).data), session._basis(mesh), atol=1e-6)
        with tempfile.TemporaryDirectory(prefix='mouth_corner_fit_') as directory:
            path = str(Path(directory) / 'corners.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
            bpy.ops.wm.open_mainfile(filepath=path)
            mesh = bpy.data.objects['SharpMouth']
            edit_one_control(mesh)
            session.reset_pose(mesh)
            np.testing.assert_allclose(session._curve_arrays(mesh.curvemorph.curve), rest, atol=1e-6)
            np.testing.assert_allclose(session._coords(session._preview(mesh).data), session._basis(mesh), atol=1e-6)
            session.rebuild_controls(mesh, 9)
            self.assertEqual(coordinates(mesh.data.shape_keys.key_blocks['KeepCornerPose'].data), saved_before)
            self.assertEqual(sum(p.handle_left_type == 'FREE' for p in mesh.curvemorph.curve.data.splines[0].bezier_points), 2)

    def test_legacy_reset_keeps_old_rest_until_explicit_rebuild(self):
        mesh, n = self.create()
        curve = mesh.curvemorph.curve
        raw = mesh[session._STATE]
        session._set_curve(curve, session._curve_arrays(curve)[:, 0])
        old_rest = session._curve_arrays(curve).copy()
        raw['rest_curve'] = old_rest.ravel().tolist()
        raw['rest_samples'] = addon.geometry.evaluate_bezier(*old_rest.transpose(1, 0, 2), raw['parameters']).ravel().tolist()
        del raw['corner_controls']
        edit_one_control(mesh)
        session.reset_pose(mesh)
        np.testing.assert_allclose(session._curve_arrays(curve), old_rest, atol=1e-6)
        np.testing.assert_allclose(session._coords(session._preview(mesh).data), session._basis(mesh), atol=1e-6)
        session.rebuild_controls(mesh, 8)
        self.assertGreater(np.max(abs(session._curve_arrays(curve) - old_rest)), .01)
        np.testing.assert_allclose(session._coords(session._preview(mesh).data), session._basis(mesh), atol=1e-6)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CornerFitTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
