"""Real Blender coverage for multi-control face authoring; isolated fixtures only."""
import importlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import bpy
import numpy as np
from mathutils import Matrix

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root.parent))
addon = importlib.import_module(root.name)
f, s = addon.facial, addon.session


def grid():
    points = [(x, y, 0) for y in range(5) for x in range(-4, 5)]
    faces = [(y*9+x, y*9+x+1, (y+1)*9+x+1, (y+1)*9+x) for y in range(4) for x in range(8)]
    data = bpy.data.meshes.new('Face')
    data.from_pydata(points, [], faces)
    obj = bpy.data.objects.new('Face', data)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    s._activate(bpy.context, obj)
    return obj


def move(entry, displacement=(0, 0, .2)):
    values = f.arrays(entry.curve) + displacement
    closed = bool(entry.curve[f.RAW]['closed'])
    f.set_curve(entry.curve, values, closed)


class FacialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        addon.register()

    def setUp(self):
        s._object_mode()
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        f.CACHE.clear()
        s._CACHE.clear()
        self.mesh = grid()
        self.basis = s._basis(self.mesh)

    def add(self, seeds=(18, 19, 20, 21), **kwargs):
        return f.create(self.mesh, self.basis[list(seeds)], list(seeds), count=4, radius=1.5, **kwargs)

    def delta(self, entry):
        f.update(self.mesh, entry, force=True)
        return s._coords(f.preview(self.mesh, entry).data) - self.basis

    def test_independent_open_closed_and_combined_save(self):
        first = self.add(name='Brow.L')
        second = self.add((23, 24, 25, 26), name='Brow.R')
        ring = self.add((3, 4, 13, 12), closed=True, name='Nostril')
        self.assertEqual(len(self.mesh.face_pose_curves), 3)
        self.assertFalse(first.curve.data.splines[0].use_cyclic_u)
        self.assertTrue(ring.curve.data.splines[0].use_cyclic_u)
        np.testing.assert_allclose(self.delta(first), 0, atol=1e-7)
        move(first)
        move(second, (0, .1, 0))
        a, b = self.delta(first), self.delta(second)
        self.assertGreater(np.linalg.norm(a), 0)
        key = f.save(self.mesh, 'Expression', reset_after=True)
        np.testing.assert_allclose(s._coords(key.data) - self.basis, a+b, atol=2e-7)
        np.testing.assert_allclose(self.delta(first), 0, atol=1e-7)
        self.assertEqual(key.value, 0)
        f.remove(self.mesh, second)
        self.assertEqual(len(self.mesh.face_pose_curves), 2)
        self.assertIn(key.name, self.mesh.data.shape_keys.key_blocks)
        self.assertTrue(first.curve)

    def test_overlap_masks_disabled_and_external_keys(self):
        a = self.add()
        b = self.add()
        group = self.mesh.vertex_groups.new(name='BrowMask')
        group.add([18, 19], .5, 'REPLACE')
        a.mask_group = group.name
        move(a)
        move(b)
        da, db = self.delta(a), self.delta(b)
        self.assertAlmostEqual(da[18, 2], .1, places=5)
        self.assertAlmostEqual(da[20, 2], 0, places=5)
        key = f.save(self.mesh, 'Both', reset_after=False)
        np.testing.assert_allclose(s._coords(key.data) - self.basis, da+db, atol=2e-7)
        b.enabled = False
        key = f.save(self.mesh, 'One', reset_after=False)
        np.testing.assert_allclose(s._coords(key.data) - self.basis, da, atol=2e-7)
        key.value = .5
        with self.assertRaisesRegex(ValueError, 'other shape key'):
            f.save(self.mesh, 'Invalid')

    def test_mirror_rest_pose_tilt_and_independent_masks(self):
        self.mesh.matrix_world = Matrix.Translation((5, -3, 1)) @ Matrix.Diagonal((2, 1, 3, 1))
        bpy.context.view_layer.update()
        a = self.add(name='Brow.L')
        group = self.mesh.vertex_groups.new(name='Brow.L')
        group.add([18, 19, 20, 21, 9], .75, 'REPLACE')
        a.mask_group = group.name
        move(a, (.1, 0, .3))
        for p in a.curve.data.splines[0].bezier_points:
            p.tilt = .25
        da = self.delta(a)
        b = f.mirror(self.mesh, a)
        self.assertEqual(b.name, 'Brow.R')
        self.assertEqual(b.curve.name, 'Brow.R')
        self.assertEqual(b.mask_group, 'Brow.R')
        db = self.delta(b)
        np.testing.assert_allclose(f.arrays(b.curve), f.arrays(a.curve) * [-1, 1, 1], atol=1e-6)
        np.testing.assert_allclose(s._curve_tilts(b.curve), -.25)
        self.assertNotEqual(a.mask_group, b.mask_group)
        for i in [18, 19, 20, 21, 9]:
            j = (i//9)*9 + (8-i%9)
            np.testing.assert_allclose(db[j], da[i]*[-1, 1, 1], atol=1e-6)
        f.reset(self.mesh, b)
        np.testing.assert_allclose(self.delta(b), 0, atol=1e-7)
        np.testing.assert_allclose(self.delta(a), da, atol=1e-7)

    def test_mirror_names_reverse_and_preserve_existing_targets(self):
        a = self.add(name='Brow.L')
        group = self.mesh.vertex_groups.new(name='Brow.L')
        group.add([18, 19, 20, 21], .75, 'REPLACE')
        a.mask_group = group.name
        b = f.mirror(self.mesh, a)
        again = f.mirror(self.mesh, a)
        self.assertEqual(again.name, 'Brow.R.001')
        self.assertEqual(again.mask_group, 'Brow.R.001')
        self.assertEqual(self.mesh.vertex_groups[b.mask_group].weight(26), .75)
        back = f.mirror(self.mesh, b)
        self.assertEqual(back.name, 'Brow.L.001')
        self.assertEqual(back.mask_group, 'Brow.L.001')
        self.assertEqual(a.name, 'Brow.L')
        for source, expected in [('Brow.L.002', 'Brow.R.002'), ('Brow_l', 'Brow_r'),
                                 ('L_Brow', 'R_Brow'), ('Brow.Left', 'Brow.Right'), ('Nose', 'Nose.Mirror')]:
            self.assertEqual(f.mirrored_name(source), expected)

    def test_fit_apply_cancel_and_deleted_curve_recovery(self):
        a = self.add()
        f.fit(self.mesh, a, 'BEGIN')
        s._object_mode()
        move(a, (0, 0, .25))
        f.fit(self.mesh, a, 'APPLY')
        np.testing.assert_allclose(self.delta(a), 0, atol=1e-7)
        rest = f.arrays(a.curve).copy()
        move(a)
        f.reset(self.mesh, a)
        np.testing.assert_allclose(f.arrays(a.curve), rest)
        f.fit(self.mesh, a, 'BEGIN')
        s._object_mode()
        a.curve.data.splines.clear()
        f.fit(self.mesh, a, 'CANCEL')
        np.testing.assert_allclose(f.arrays(a.curve), rest)
        bpy.data.objects.remove(a.curve, do_unlink=True)
        f.update_all(self.mesh)
        self.assertIn('missing', a.error)
        f.remove(self.mesh, a)
        self.assertEqual(len(self.mesh.face_pose_curves), 0)

    def test_fit_subdivision_rebinds_uneven_segments_and_new_points_pose(self):
        a = self.add()
        saved = self.mesh.shape_key_add(name='Existing', from_mix=False)
        saved.value = 0
        saved_before = s._coords(saved.data).copy()
        f.fit(self.mesh, a, 'BEGIN')
        bpy.ops.curve.select_all(action='DESELECT')
        for point in a.curve.data.splines[0].bezier_points[:2]:
            point.select_control_point = point.select_left_handle = point.select_right_handle = True
        bpy.ops.curve.subdivide(number_cuts=1)
        self.assertEqual(bpy.ops.facepose.action(action='APPLY_FIT'), {'FINISHED'})
        self.assertEqual(len(f.arrays(a.curve)), 5)
        np.testing.assert_allclose(a.curve[f.RAW]['parameters'], [0, .5, .75, 1], atol=1e-6)
        np.testing.assert_allclose(self.delta(a), 0, atol=1e-7)
        a.curve.data.splines[0].bezier_points[2].co.z += .2
        np.testing.assert_allclose(self.delta(a)[19], [0, 0, .2], atol=1e-6)
        f.save(self.mesh, 'Refitted Brow', reset_after=True)
        self.assertEqual(len(f.arrays(a.curve)), 5)
        np.testing.assert_allclose(self.delta(a), 0, atol=1e-7)
        np.testing.assert_array_equal(s._coords(saved.data), saved_before)
        # On a denser path, a newly inserted control drives nearby mesh seeds too.
        seeds = list(range(18, 27))
        b = f.create(self.mesh, self.basis[seeds], seeds, count=4, radius=1)
        f.fit(self.mesh, b, 'BEGIN')
        bpy.ops.curve.select_all(action='DESELECT')
        for point in b.curve.data.splines[0].bezier_points[:2]:
            point.select_control_point = point.select_left_handle = point.select_right_handle = True
        bpy.ops.curve.subdivide(number_cuts=1)
        f.fit(self.mesh, b, 'APPLY')
        b.curve.data.splines[0].bezier_points[1].co.z += .2
        self.assertGreater(np.linalg.norm(self.delta(b)[[19, 20]]), .01)

    def test_fit_delete_open_endpoint_cancel_and_failed_apply(self):
        a = self.add()
        original = f.arrays(a.curve).copy()
        f.fit(self.mesh, a, 'BEGIN')
        bpy.ops.curve.select_all(action='DESELECT')
        a.curve.data.splines[0].bezier_points[0].select_control_point = True
        bpy.ops.curve.delete(type='VERT')
        s._object_mode()
        edited = f.arrays(a.curve).copy()
        raw_before = a.curve[f.RAW].to_dict()
        with patch.object(f, 'update', side_effect=RuntimeError('Injected failure')):
            with self.assertRaisesRegex(RuntimeError, 'Injected failure'):
                f.fit(self.mesh, a, 'APPLY')
        self.assertTrue(a.fitting)
        self.assertEqual(a.curve[f.RAW].to_dict(), raw_before)
        np.testing.assert_array_equal(f.arrays(a.curve), edited)
        f.fit(self.mesh, a, 'CANCEL')
        np.testing.assert_array_equal(f.arrays(a.curve), original)
        f.fit(self.mesh, a, 'BEGIN')
        bpy.ops.curve.select_all(action='DESELECT')
        for point in a.curve.data.splines[0].bezier_points[:2]:
            point.select_control_point = True
        bpy.ops.curve.delete(type='VERT')
        f.fit(self.mesh, a, 'APPLY')
        self.assertEqual(len(f.arrays(a.curve)), 2)
        np.testing.assert_allclose(self.delta(a), 0, atol=1e-7)
        move(a)
        self.assertGreater(np.linalg.norm(self.delta(a)), .1)

    def test_fit_closed_seam_deletion_and_reload(self):
        seeds = [0, 1, 2, 11, 20, 19, 18, 9]
        a = f.create(self.mesh, self.basis[seeds], seeds, count=8, radius=1, closed=True)
        f.fit(self.mesh, a, 'BEGIN')
        bpy.ops.curve.select_all(action='DESELECT')
        a.curve.data.splines[0].bezier_points[0].select_control_point = True
        bpy.ops.curve.delete(type='VERT')
        f.fit(self.mesh, a, 'APPLY')
        self.assertEqual(len(f.arrays(a.curve)), 7)
        np.testing.assert_allclose(self.delta(a), 0, atol=1e-7)
        # The retained corner at original parameter 1/4 stays on the same spatial corner.
        samples = np.asarray(a.curve[f.RAW]['rest_samples']).reshape((-1, 3))
        np.testing.assert_allclose(samples[2], self.basis[2], atol=1e-6)
        path = str(Path(tempfile.gettempdir()) / 'face_refit_topology.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=path)
        self.mesh = bpy.data.objects['Face']
        a = self.mesh.face_pose_curves[0]
        self.assertEqual(len(f.arrays(a.curve)), 7)
        move(a)
        self.assertGreater(np.linalg.norm(self.delta(a)), .1)
        f.reset(self.mesh, a)
        np.testing.assert_allclose(self.delta(a), 0, atol=1e-7)

    def test_fit_changed_count_apply_undo_redo_and_cancel(self):
        a = self.add()
        original = f.arrays(a.curve).copy()
        bpy.ops.ed.undo_push(message='Before fit topology edit')
        self.assertEqual(bpy.ops.facepose.action(action='FIT'), {'FINISHED'})
        bpy.ops.curve.select_all(action='DESELECT')
        for point in a.curve.data.splines[0].bezier_points[:2]:
            point.select_control_point = point.select_left_handle = point.select_right_handle = True
        bpy.ops.curve.subdivide(number_cuts=1)
        s._object_mode()
        bpy.ops.ed.undo_push(message='Edited fit topology')
        self.assertEqual(bpy.ops.facepose.action(action='APPLY_FIT'), {'FINISHED'})
        bpy.ops.ed.undo_push(message='Applied fit topology')
        bpy.ops.ed.undo()
        self.mesh = bpy.data.objects['Face']
        a = self.mesh.face_pose_curves[0]
        s._tick()
        self.assertTrue(a.fitting)
        self.assertEqual(len(f.arrays(a.curve)), 5)
        self.assertEqual(len(a.curve[f.RAW]['rest_curve']), original.size)
        bpy.ops.ed.redo()
        self.mesh = bpy.data.objects['Face']
        a = self.mesh.face_pose_curves[0]
        s._tick()
        self.assertFalse(a.fitting)
        self.assertEqual(len(a.curve[f.RAW]['rest_curve']), 5*9)
        np.testing.assert_allclose(self.delta(a), 0, atol=1e-7)
        bpy.ops.ed.undo()
        self.mesh = bpy.data.objects['Face']
        a = self.mesh.face_pose_curves[0]
        self.assertEqual(bpy.ops.facepose.action(action='CANCEL_FIT'), {'FINISHED'})
        np.testing.assert_allclose(f.arrays(a.curve), original, atol=1e-7)

    def test_fit_dense_subdivision_can_still_be_mirrored(self):
        a = self.add(name='Dense.L')
        f.fit(self.mesh, a, 'BEGIN')
        bpy.ops.curve.select_all(action='SELECT')
        bpy.ops.curve.subdivide(number_cuts=24)
        f.fit(self.mesh, a, 'APPLY')
        count = len(f.arrays(a.curve))
        self.assertGreater(count, 64)
        b = f.mirror(self.mesh, a)
        self.assertEqual(len(f.arrays(b.curve)), count)
        np.testing.assert_allclose(self.delta(b), 0, atol=1e-7)

    def test_grease_pencil_real_drawing_and_input_preserved(self):
        data = bpy.data.grease_pencils.new('Drawn Brows')
        grease = bpy.data.objects.new('Drawn Brows', data)
        bpy.context.scene.collection.objects.link(grease)
        layer = data.layers.new('Brows', set_active=True)
        drawing = layer.frames.new(1).drawing
        drawing.add_strokes([3, 3])
        position = drawing.attributes['position']
        coords = [-4, 2, .1, -2.5, 2.3, .1, -1, 2, .1, 1, 2, .1, 2.5, 2.3, .1, 4, 2, .1]
        position.data.foreach_set('vector', coords)
        settings = bpy.context.scene.face_pose_settings
        settings.grease = grease
        bpy.context.scene.curvemorph_settings.target = self.mesh
        count = f.create_strokes(bpy.context)
        self.assertEqual(count, 2)
        self.assertEqual(len(drawing.curve_offsets), 3)
        self.assertEqual(len(position.data), 6)
        for a in self.mesh.face_pose_curves:
            self.assertFalse(a.curve.data.splines[0].use_cyclic_u)
            np.testing.assert_allclose(self.delta(a), 0, atol=1e-7)
            move(a)
            self.assertGreater(np.linalg.norm(self.delta(a)), .1)

    def test_save_reopen_persists_all_controls_and_fitting(self):
        a = self.add()
        b = self.add((23, 24, 25, 26))
        move(a)
        expected = self.delta(a)
        f.fit(self.mesh, b, 'BEGIN')
        s._object_mode()
        path = str(Path(tempfile.gettempdir()) / 'face_pose_regression.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=path)
        self.mesh = bpy.data.objects['Face']
        self.assertEqual(len(self.mesh.face_pose_curves), 2)
        a, b = self.mesh.face_pose_curves
        self.assertTrue(b.fitting)
        f.fit(self.mesh, b, 'CANCEL')
        np.testing.assert_allclose(self.delta(a), expected, atol=1e-7)

    def test_failed_creation_and_mirror_leave_no_helpers(self):
        a = self.add()
        before = (len(bpy.data.objects), len(self.mesh.face_pose_curves), len(self.mesh.data.shape_keys.key_blocks))
        with self.assertRaises(ValueError):
            self.add((0, 2))
        self.mesh.data.vertices[26].co.x += .5
        # Basis remains unchanged; matching uses the Basis, as intended.
        basis_key = self.mesh.data.shape_keys.reference_key
        basis_key.data[26].co.x += .5
        with self.assertRaises(ValueError):
            f.mirror(self.mesh, a)
        self.assertEqual(before, (len(bpy.data.objects), len(self.mesh.face_pose_curves), len(self.mesh.data.shape_keys.key_blocks)))

    def test_legacy_mouth_and_new_control_combine_and_mirror(self):
        sys.path.insert(0, str(root / 'tests'))
        from test_blender_integration import fixture, select_loop
        mesh, n, _ = fixture('Legacy')
        select_loop(mesh, n)
        s.create_session(bpy.context)
        self.mesh, self.basis = mesh, s._basis(mesh)
        mouth = mesh.curvemorph.curve
        mouth.location.z += .2
        s.update_session(mesh, force=True)
        a = f.create(mesh, self.basis[[32, 33, 34, 35]], [32, 33, 34, 35], count=4, radius=.2)
        move(a, (0, .1, 0))
        expected = s._coords(s._preview(mesh).data) - self.basis + self.delta(a)
        key = f.save(mesh, 'Whole Face', reset_after=False)
        np.testing.assert_allclose(s._coords(key.data)-self.basis, expected, atol=2e-7)
        legacy = next(e for e in mesh.face_pose_curves if e.legacy)
        copy = f.mirror(mesh, legacy, tolerance=.001)
        self.assertTrue(copy.curve.data.splines[0].use_cyclic_u)
        self.assertEqual(len(mesh.face_pose_curves), 3)
        f.remove(mesh, copy)
        self.assertTrue(mesh.curvemorph.curve)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(FacialTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
