"""Independent twist mask and common falloff profiles through real sessions."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from test_blender_integration import *
from test_blender_setup import stored_setup
from test_blender_tilt import tilt_one
import numpy as np


class TwistMaskFalloffTests(unittest.TestCase):
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
        self.mesh, self.n, _ = fixture('MaskFalloffCharacter')
        stored_setup(self.mesh, self.n)
        settings = bpy.context.scene.curvemorph_settings
        settings.falloff_type = 'SMOOTH'
        settings.random_seed = 0
        addon.setup.create_controls(bpy.context)
        self.neutral = np.array(coordinates(self.mesh.data.vertices))

    def preview(self):
        return np.array(coordinates(session._preview(self.mesh).data))

    def test_twist_mask_does_not_mask_translation_and_weights_update_live(self):
        mesh = self.mesh
        edit_one_control(mesh)
        translation = self.preview()
        tilt_one(mesh)
        full = self.preview()
        session._object_mode()
        group = mesh.vertex_groups.new(name='TwistOnly')
        group.add([self.n], .25, 'REPLACE')
        mesh.curvemorph.twist_mask_group = group.name
        session.update_session(mesh)
        expected = translation.copy()
        expected[self.n] += .25*(full[self.n]-translation[self.n])
        np.testing.assert_allclose(self.preview(), expected, atol=1e-6)
        group.add([self.n], .75, 'REPLACE')
        session.update_session(mesh)
        expected[self.n] = translation[self.n] + .75*(full[self.n]-translation[self.n])
        np.testing.assert_allclose(self.preview(), expected, atol=1e-6)
        saved = session.save_shape_key(mesh, 'MaskedTwist', reset_after=False)
        self.assertEqual(saved.vertex_group, '')
        np.testing.assert_allclose(coordinates(saved.data), expected, atol=1e-6)
        mesh.curvemorph.twist_mask_group = ''
        session.update_session(mesh)
        np.testing.assert_allclose(self.preview(), full, atol=1e-6)
        np.testing.assert_allclose(coordinates(saved.data), expected, atol=1e-6)

    def test_overall_and_twist_masks_multiply_without_overriding_each_other(self):
        mesh = self.mesh
        edit_one_control(mesh)
        translation = self.preview()
        tilt_one(mesh)
        full = self.preview()
        session._object_mode()
        overall = mesh.vertex_groups.new(name='Overall')
        overall.add([self.n], .5, 'REPLACE')
        twist = mesh.vertex_groups.new(name='Twist')
        twist.add([self.n], .25, 'REPLACE')
        mesh.curvemorph.use_mask = True
        mesh.curvemorph.mask_group = overall.name
        mesh.curvemorph.twist_mask_group = twist.name
        session.update_session(mesh)
        expected = self.neutral.copy()
        expected[self.n] += .5*(translation[self.n]-self.neutral[self.n] + .25*(full[self.n]-translation[self.n]))
        np.testing.assert_allclose(self.preview(), expected, atol=1e-6)

    def test_missing_twist_group_does_not_silently_unmask(self):
        mesh = self.mesh
        tilt_one(mesh)
        session._object_mode()
        group = mesh.vertex_groups.new(name='Twist')
        group.add([self.n], .25, 'REPLACE')
        mesh.curvemorph.twist_mask_group = group.name
        session.update_session(mesh)
        before = self.preview()
        mesh.vertex_groups.remove(group)
        with self.assertRaisesRegex(ValueError, 'twist mask group is missing'):
            session.update_session(mesh)
        np.testing.assert_array_equal(self.preview(), before)

    def test_all_profiles_update_without_rebinding_and_random_is_stable(self):
        mesh = self.mesh
        edit_one_control(mesh)
        tilt_one(mesh)
        binding = session._CACHE[mesh.curvemorph.token]['binding']
        for name in addon.geometry.FALLOFF_TYPES:
            with self.subTest(profile=name):
                mesh.curvemorph.falloff_type = name
                session.update_session(mesh)
                self.assertIs(session._CACHE[mesh.curvemorph.token]['binding'], binding)
                self.assertTrue(np.isfinite(self.preview()).all())
                self.assertGreater(np.max(abs(self.preview()-self.neutral)), .01)
        mesh.curvemorph.falloff_type = 'RANDOM'
        session.update_session(mesh)
        first = self.preview()
        session.update_session(mesh, force=True)
        np.testing.assert_array_equal(self.preview(), first)
        mesh.curvemorph.random_seed += 1
        session.update_session(mesh)
        self.assertGreater(np.max(abs(self.preview()-first)), 1e-4)

    def test_creation_settings_and_reload_persist_masks_profile_seed(self):
        mesh = self.mesh
        session.finish_session(mesh)
        group = mesh.vertex_groups.new(name='Twist')
        group.add([self.n], .4, 'REPLACE')
        mesh.curvemorph.twist_mask_group = group.name
        settings = bpy.context.scene.curvemorph_settings
        settings.falloff_type = 'RANDOM'
        settings.random_seed = 57
        addon.setup.create_controls(bpy.context)
        self.assertEqual(mesh.curvemorph.falloff_type, 'RANDOM')
        self.assertEqual(mesh.curvemorph.random_seed, 57)
        edit_one_control(mesh)
        tilt_one(mesh)
        before = self.preview()
        session._object_mode()
        with tempfile.TemporaryDirectory(prefix='mouth_twist_mask_') as directory:
            path = str(Path(directory) / 'masked.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
            bpy.ops.wm.open_mainfile(filepath=path)
            self.mesh = mesh = bpy.data.objects['MaskFalloffCharacter']
            self.assertEqual(mesh.curvemorph.twist_mask_group, 'Twist')
            self.assertEqual(mesh.curvemorph.falloff_type, 'RANDOM')
            self.assertEqual(mesh.curvemorph.random_seed, 57)
            session.update_session(mesh)
            np.testing.assert_allclose(self.preview(), before, atol=1e-6)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(TwistMaskFalloffTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
