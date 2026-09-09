"""Setup refinement regressions; run with Blender --background --factory-startup."""
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parent))
from test_blender_integration import *

setup = addon.setup


def vertices(mesh, indices, kind='CORNERS'):
    setup.begin_edit(bpy.context, mesh, kind)
    bm = bmesh.from_edit_mesh(mesh.data)
    bm.verts.ensure_lookup_table()
    for face in bm.faces:
        face.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for vertex in bm.verts:
        vertex.select_set(False)
    for index in indices:
        bm.verts[index].select_set(True)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(mesh.data, loop_triangles=False, destructive=False)


def stored_setup(mesh, n, corners=None):
    select_loop(mesh, n)
    assert bpy.ops.curvemorph.setup_capture(kind='LOOP') == {'FINISHED'}
    vertices(mesh, corners or (0, n // 2))
    assert bpy.ops.curvemorph.setup_capture(kind='CORNERS') == {'FINISHED'}


class SetupTests(unittest.TestCase):
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

    def test_explicit_corners_pin_controls_for_even_and_odd_counts(self):
        for count in (4, 5, 8, 9, 16):
            with self.subTest(count=count):
                mesh, n, _ = fixture('Corners' + str(count))
                neutral = coordinates(mesh.data.vertices)
                corners = (3, 18)
                stored_setup(mesh, n, corners)
                bpy.context.scene.curvemorph_settings.control_count = count
                setup.create_controls(bpy.context)
                co = [tuple(p.co) for p in mesh.curvemorph.curve.data.splines[0].bezier_points]
                for index in corners:
                    self.assertLess(min((Vector(p) - Vector(neutral[index])).length for p in co), 1e-6)
                self.assertLess(max_difference(key_coordinates(mesh, mesh.curvemorph.preview_name), neutral), 1e-6)
                edit_one_control(mesh)
                delta = Vector(key_coordinates(mesh, mesh.curvemorph.preview_name)[corners[0]]) - Vector(neutral[corners[0]])
                self.assertAlmostEqual(delta.z, 0.25, places=5)
                session.finish_session(mesh)

    def test_bad_corner_selection_keeps_existing_setup(self):
        mesh, n, _ = fixture()
        stored_setup(mesh, n)
        before = list(setup.read(mesh)['corners'])
        for choice in ((0,), (0, 1), (0, n), (0, 4, 8)):
            with self.subTest(selection=choice):
                vertices(mesh, choice)
                with self.assertRaises(ValueError):
                    setup.capture_corners(bpy.context)
                self.assertEqual(list(setup.read(mesh)['corners']), before)
                self.assertFalse(mesh.curvemorph.token)

    def test_optional_mask_scales_displacement_and_updates_live(self):
        mesh, n, _ = fixture()
        neutral = coordinates(mesh.data.vertices)
        stored_setup(mesh, n)
        group = mesh.vertex_groups.new(name='CurveMorph_Affected')
        group.add([2], 0.37, 'REPLACE')
        vertices(mesh, (0, n, n + 1), 'MASK')
        mask = setup.create_mask(bpy.context)
        self.assertNotEqual(mask.name, group.name)
        self.assertAlmostEqual(group.weight(2), 0.37, places=5)
        setup.create_controls(bpy.context)
        edit_one_control(mesh)
        name = mesh.curvemorph.preview_name
        masked = key_coordinates(mesh, name)
        self.assertGreater(max_difference(masked, neutral, [0]), 0.1)
        self.assertEqual(max_difference(masked, neutral, [i for i in range(len(neutral)) if i not in (0, n, n+1)]), 0)
        mask.add([0], 0.25, 'REPLACE')
        session.update_session(mesh)  # No force: changed group weights must invalidate the preview signature.
        weighted = key_coordinates(mesh, name)
        self.assertAlmostEqual(weighted[0][2] - neutral[0][2], 0.25 * (masked[0][2] - neutral[0][2]), places=6)
        saved = session.save_shape_key(mesh, 'MaskedPose', reset_after=False)
        self.assertEqual(saved.vertex_group, '')  # Weights are baked once, not applied twice.
        self.assertLess(max_difference(coordinates(saved.data), weighted), 1e-6)
        mesh.curvemorph.use_mask = False
        session.update_session(mesh)
        full = key_coordinates(mesh, name)
        self.assertGreater(max_difference(full, neutral, [1]), 0.01)
        self.assertLess(max_difference(coordinates(saved.data), weighted), 1e-6)

    def test_mask_refinement_pauses_and_resumes_same_control_pose(self):
        mesh, n, _ = fixture()
        stored_setup(mesh, n)
        setup.create_controls(bpy.context)
        edit_one_control(mesh)
        curve_before = session._curve_arrays(mesh.curvemorph.curve).copy()
        vertices(mesh, (0, 1, 2), 'MASK')
        session._tick()
        self.assertTrue(mesh.curvemorph.setup_editing)
        self.assertEqual(session._preview(mesh).value, 0)
        self.assertEqual(mesh.curvemorph.error, '')
        setup.create_mask(bpy.context)
        session.edit_controls(bpy.context, mesh)
        self.assertFalse(mesh.curvemorph.setup_editing)
        self.assertEqual(session._preview(mesh).value, 1)
        self.assertTrue((curve_before == session._curve_arrays(mesh.curvemorph.curve)).all())
        vertices(mesh, (0,), 'MASK')
        setup.create_mask(bpy.context, 'REMOVE')
        session.edit_controls(bpy.context, mesh)
        self.assertEqual(session._preview(mesh).data[0].co, mesh.data.shape_keys.reference_key.data[0].co)

    def test_refine_loop_and_corners_then_apply_preserves_saved_keys(self):
        mesh, n, _ = fixture()
        stored_setup(mesh, n)
        setup.create_controls(bpy.context)
        edit_one_control(mesh)
        saved = session.save_shape_key(mesh, 'KeepMe', reset_after=False)
        saved_co = coordinates(saved.data)
        curve_co = session._curve_arrays(mesh.curvemorph.curve).copy()
        setup.begin_edit(bpy.context, mesh, 'LOOP')
        select_edges(mesh, lambda a, b: n <= a < 2*n and n <= b < 2*n)
        setup.capture_loop(bpy.context)
        self.assertEqual(list(setup.read(mesh)['corners']), [])
        self.assertTrue(setup.pending(mesh))
        self.assertTrue((curve_co == session._curve_arrays(mesh.curvemorph.curve)).all())
        vertices(mesh, (n, n+n//2))
        setup.capture_corners(bpy.context)
        setup.apply_setup(bpy.context, mesh)
        self.assertFalse(setup.pending(mesh))
        self.assertFalse(mesh.curvemorph.setup_editing)
        self.assertLess(max_difference(coordinates(saved.data), saved_co), 1e-6)
        self.assertLess(max_difference(coordinates(session._preview(mesh).data), coordinates(mesh.data.shape_keys.reference_key.data)), 1e-6)
        loop = list(setup.read(mesh)['loop'])
        session.finish_session(mesh)
        self.assertEqual(list(setup.read(mesh)['loop']), loop)
        setup.create_controls(bpy.context)  # No reselection needed after finishing.
        self.assertEqual(list(mesh[session._STATE]['loop']), loop)

    def test_apply_failure_restores_curve_and_preview(self):
        mesh, n, _ = fixture()
        stored_setup(mesh, n)
        setup.create_controls(bpy.context)
        edit_one_control(mesh)
        preview_before = coordinates(session._preview(mesh).data)
        curve_before = session._curve_arrays(mesh.curvemorph.curve).copy()
        raw_before = mesh[session._STATE].to_dict()
        old = session._set_curve
        def fail_after_creating(*args):
            old(*args)
            raise RuntimeError('Injected setup failure')
        with patch.object(session, '_set_curve', fail_after_creating):
            with self.assertRaises(RuntimeError):
                setup.apply_setup(bpy.context, mesh)
        self.assertEqual(mesh[session._STATE].to_dict(), raw_before)
        self.assertTrue((curve_before == session._curve_arrays(mesh.curvemorph.curve)).all())
        self.assertLess(max_difference(coordinates(session._preview(mesh).data), preview_before), 1e-6)

    def test_missing_mask_fails_without_unmasking_mesh(self):
        mesh, n, _ = fixture()
        stored_setup(mesh, n)
        vertices(mesh, (0,), 'MASK')
        mask = setup.create_mask(bpy.context)
        setup.create_controls(bpy.context)
        edit_one_control(mesh)
        before = coordinates(session._preview(mesh).data)
        mesh.vertex_groups.remove(mask)
        with self.assertRaisesRegex(ValueError, 'group is missing'):
            session.update_session(mesh)
        self.assertLess(max_difference(coordinates(session._preview(mesh).data), before), 1e-6)

    def test_setup_without_session_survives_save_reload(self):
        mesh, n, _ = fixture('StoredSetup')
        stored_setup(mesh, n)
        vertices(mesh, (0, 1), 'MASK')
        group = setup.create_mask(bpy.context)
        group_name = group.name
        with tempfile.TemporaryDirectory(prefix='mouth_setup_') as directory:
            path = str(Path(directory) / 'setup.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
            bpy.ops.wm.open_mainfile(filepath=path)
            mesh = bpy.data.objects['StoredSetup']
            self.assertEqual(list(setup.read(mesh)['corners']), [0, n//2])
            self.assertTrue(mesh.curvemorph.use_mask)
            self.assertEqual(mesh.curvemorph.mask_group, group_name)
            setup.create_controls(bpy.context)
            self.assertTrue(mesh.curvemorph.token)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SetupTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
