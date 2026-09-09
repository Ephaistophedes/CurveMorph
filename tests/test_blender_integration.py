"""Regression coverage in an isolated Blender process.

Run from any directory with:
  blender --background --factory-startup --python tests/test_blender_integration.py

Fixtures are generated here; no user's .blend or running Blender is opened.
"""

import importlib
import math
from pathlib import Path
import sys
import tempfile
import unittest

import bmesh
import bpy
from mathutils import Euler, Vector


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))
addon = importlib.import_module(ADDON_ROOT.name)
session = importlib.import_module(f"{ADDON_ROOT.name}.session")


def activate(obj):
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def fixture(name="Character", angular_count=32):
    """Open mouth annulus plus a nearby, disconnected triangle."""
    verts = []
    ring_scales = (1.0, 1.15, 1.5, 2.5, 4.0)
    for scale in ring_scales:
        for index in range(angular_count):
            angle = 2.0 * math.pi * index / angular_count
            verts.append((scale * math.cos(angle), 0.45 * scale * math.sin(angle), 0.0))
    faces = []
    for ring in range(len(ring_scales) - 1):
        for index in range(angular_count):
            next_index = (index + 1) % angular_count
            faces.append((
                ring * angular_count + index,
                (ring + 1) * angular_count + index,
                (ring + 1) * angular_count + next_index,
                ring * angular_count + next_index,
            ))
    island_start = len(verts)
    verts.extend(((1.0, 0.0, 0.02), (1.02, 0.01, 0.02), (0.99, 0.025, 0.02)))
    faces.append((island_start, island_start + 1, island_start + 2))
    data = bpy.data.meshes.new(name + "Mesh")
    data.from_pydata(verts, [], faces)
    data.update()
    mesh = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(mesh)
    return mesh, angular_count, tuple(range(island_start, island_start + 3))


def select_edges(mesh, predicate):
    activate(mesh)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.context.tool_settings.mesh_select_mode = (False, True, False)
    bm = bmesh.from_edit_mesh(mesh.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    for face in bm.faces:
        face.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for vert in bm.verts:
        vert.select_set(False)
    for edge in bm.edges:
        a, b = (vert.index for vert in edge.verts)
        if predicate(a, b):
            edge.select_set(True)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(mesh.data)


def select_loop(mesh, angular_count):
    select_edges(mesh, lambda a, b: a < angular_count and b < angular_count)


def coordinates(points):
    return tuple(tuple(point.co) for point in points)


def evaluated_coordinates(mesh):
    bpy.context.view_layer.update()
    evaluated = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
    result = evaluated.to_mesh()
    try:
        return coordinates(result.vertices)
    finally:
        evaluated.to_mesh_clear()


def max_difference(a, b, indices=None):
    if indices is None:
        indices = range(len(a))
    return max((Vector(a[index]) - Vector(b[index])).length for index in indices)


def key_coordinates(mesh, name):
    return coordinates(mesh.data.shape_keys.key_blocks[name].data)


def edit_one_control(mesh, translation=(0.0, 0.0, 0.25)):
    curve = mesh.curvemorph.curve
    activate(curve)
    for index, point in enumerate(curve.data.splines[0].bezier_points):
        selected = index == 0
        point.select_control_point = selected
        point.select_left_handle = selected
        point.select_right_handle = selected
    session.edit_controls(bpy.context, mesh)
    assert bpy.context.object == curve and curve.mode == "EDIT"
    bpy.ops.transform.translate(value=translation, orient_type="GLOBAL")
    session.update_session(mesh, force=True)
    bpy.context.view_layer.update()


class CurveMorphIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        addon.register()

    @classmethod
    def tearDownClass(cls):
        if bpy.context.object and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        addon.unregister()

    def setUp(self):
        if bpy.context.object and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)

    def assertCoordinatesEqual(self, actual, expected, message="", tolerance=2e-6):
        self.assertEqual(len(actual), len(expected), message)
        self.assertLessEqual(max_difference(actual, expected), tolerance, message)

    def create(self, mesh, angular_count, count=8, radius=0.38):
        select_loop(mesh, angular_count)
        session.create_session(bpy.context, count, radius, 2.0)
        self.assertTrue(mesh.curvemorph.token)
        self.assertEqual(mesh.curvemorph.control_count, count)
        self.assertAlmostEqual(mesh.curvemorph.radius, radius, places=5)
        self.assertAlmostEqual(mesh.curvemorph.falloff, 2.0, places=5)
        curve = mesh.curvemorph.curve
        self.assertIsNotNone(curve)
        self.assertEqual(curve.type, "CURVE")
        self.assertEqual(curve.curvemorph_target, mesh)
        self.assertEqual(len(curve.data.splines), 1)
        self.assertEqual(curve.data.splines[0].type, "BEZIER")
        self.assertTrue(curve.data.splines[0].use_cyclic_u)
        self.assertEqual(len(curve.data.splines[0].bezier_points), count)
        return curve

    def test_pose_save_reset_and_preserve_existing_data(self):
        mesh, n, island = fixture()
        mesh.shape_key_add(name="Basis")
        existing = mesh.shape_key_add(name="ExistingSmile")
        existing.data[2].co.z = 0.12
        existing.value = 0.0
        existing_coordinates = coordinates(existing.data)
        modifier = mesh.modifiers.new("ExistingSubdivision", "SUBSURF")
        modifier.levels = 1
        modifier.render_levels = 2
        modifier.show_viewport = True
        original_evaluated = evaluated_coordinates(mesh)
        original_mesh = coordinates(mesh.data.vertices)
        self.create(mesh, n)
        preview_name = mesh.curvemorph.preview_name
        self.assertCoordinatesEqual(evaluated_coordinates(mesh), original_evaluated,
                                    "Creating controls must not reshape the character")
        self.assertCoordinatesEqual(key_coordinates(mesh, preview_name), original_mesh)

        edit_one_control(mesh)
        posed = key_coordinates(mesh, preview_name)
        self.assertGreater(max_difference(posed, original_mesh, range(n)), 0.03,
                           "Dragging a Bezier point in Edit Mode must update the preview")
        self.assertEqual(max_difference(posed, original_mesh, island), 0.0,
                         "Disconnected geometry inside the radius must stay still")
        outer_ring = range(4 * n, 5 * n)
        self.assertEqual(max_difference(posed, original_mesh, outer_ring), 0.0,
                         "Vertices beyond the influence radius must stay still")
        saved = session.save_shape_key(mesh, "TestSmile", reset_after=True)
        self.assertEqual(saved.name, "TestSmile")
        self.assertCoordinatesEqual(coordinates(saved.data), posed,
                                    "Saved shape key must contain the posed coordinates")
        self.assertEqual(saved.value, 0.0)
        self.assertCoordinatesEqual(key_coordinates(mesh, preview_name), original_mesh,
                                    "Save with reset must return the live preview to neutral")
        self.assertCoordinatesEqual(key_coordinates(mesh, "ExistingSmile"), existing_coordinates)
        self.assertEqual(mesh.data.shape_keys.key_blocks["ExistingSmile"].value, 0.0)
        self.assertEqual(len(mesh.modifiers), 1)
        self.assertEqual(mesh.modifiers[0].name, "ExistingSubdivision")
        self.assertEqual(mesh.modifiers[0].levels, 1)
        self.assertTrue(mesh.modifiers[0].show_viewport)
        self.assertCoordinatesEqual(evaluated_coordinates(mesh), original_evaluated)
        session.finish_session(mesh)
        self.assertIn("TestSmile", mesh.data.shape_keys.key_blocks)
        self.assertIn("ExistingSmile", mesh.data.shape_keys.key_blocks)
        self.assertNotIn(preview_name, mesh.data.shape_keys.key_blocks)
        self.assertFalse(mesh.curvemorph.token)

    def test_counts_rebuild_and_nonuniform_object_transform(self):
        mesh, n, island = fixture()
        mesh.location = (2.0, -3.0, 1.0)
        mesh.rotation_euler = Euler((0.3, -0.2, 0.65))
        mesh.scale = (2.0, 0.7, 1.3)
        neutral = coordinates(mesh.data.vertices)
        self.create(mesh, n, count=4)
        self.assertCoordinatesEqual(key_coordinates(mesh, mesh.curvemorph.preview_name), neutral)
        for count in (12, 6, 16):
            with self.subTest(control_count=count):
                session.rebuild_controls(mesh, count)
                self.assertEqual(mesh.curvemorph.control_count, count)
                self.assertEqual(len(mesh.curvemorph.curve.data.splines[0].bezier_points), count)
                edit_one_control(mesh, translation=(0.04, -0.06, 0.2))
                posed = key_coordinates(mesh, mesh.curvemorph.preview_name)
                self.assertGreater(max_difference(posed, neutral, range(n)), 0.02)
                self.assertEqual(max_difference(posed, neutral, island), 0.0)
                session.reset_pose(mesh)
                self.assertCoordinatesEqual(key_coordinates(mesh, mesh.curvemorph.preview_name), neutral)
        session.finish_session(mesh)

    def test_zero_radius_only_moves_selected_loop(self):
        mesh, n, island = fixture()
        neutral = coordinates(mesh.data.vertices)
        self.create(mesh, n)
        mesh.curvemorph.radius = 0.0
        edit_one_control(mesh)
        posed = key_coordinates(mesh, mesh.curvemorph.preview_name)
        self.assertGreater(max_difference(posed, neutral, range(n)), 0.03)
        self.assertEqual(max_difference(posed, neutral, range(n, len(neutral))), 0.0)
        session.finish_session(mesh)

    def test_live_radius_and_falloff_recompute_existing_pose(self):
        mesh, n, island = fixture()
        neutral = coordinates(mesh.data.vertices)
        self.create(mesh, n, radius=0.08)
        edit_one_control(mesh)
        narrow = key_coordinates(mesh, mesh.curvemorph.preview_name)
        self.assertEqual(max_difference(narrow, neutral, [n]), 0.0)
        mesh.curvemorph.radius = 0.38
        session.update_session(mesh, force=True)
        broad = key_coordinates(mesh, mesh.curvemorph.preview_name)
        self.assertGreater(max_difference(broad, neutral, [n]), 0.005)
        mesh.curvemorph.falloff = 4.0
        session.update_session(mesh, force=True)
        concentrated = key_coordinates(mesh, mesh.curvemorph.preview_name)
        self.assertLess(max_difference(concentrated, neutral, [n]),
                        max_difference(broad, neutral, [n]))
        self.assertCoordinatesEqual(concentrated[:n], broad[:n])
        mesh.curvemorph.radius = 0.0
        session.update_session(mesh, force=True)
        loop_only = key_coordinates(mesh, mesh.curvemorph.preview_name)
        self.assertEqual(max_difference(loop_only, neutral, range(n, len(neutral))), 0.0,
                         "Shrinking influence must clear displacement from formerly affected vertices")
        self.assertCoordinatesEqual(loop_only[:n], broad[:n])
        session.finish_session(mesh)

    def test_renaming_and_cleanup_uses_ownership(self):
        mesh, n, island = fixture()
        curve = self.create(mesh, n)
        old_curve_name = curve.name
        mesh.name = "RenamedCharacter"
        curve.name = "RenamedBezierControls"
        unrelated_data = bpy.data.curves.new("UnrelatedCurveData", "CURVE")
        unrelated_curve = bpy.data.objects.new(old_curve_name, unrelated_data)
        bpy.context.collection.objects.link(unrelated_curve)
        unrelated_mesh, _, _ = fixture(name="Character")
        unrelated_mesh.shape_key_add(name="Basis")
        preview_name = mesh.curvemorph.preview_name
        unrelated_key = unrelated_mesh.shape_key_add(name=preview_name)
        unrelated_key.data[0].co.z = 0.8
        unrelated_coordinates = coordinates(unrelated_key.data)
        edit_one_control(mesh)
        saved = session.save_shape_key(mesh, "RenamedSessionPose", reset_after=False)
        self.assertGreater(max_difference(coordinates(saved.data), coordinates(mesh.data.vertices)), 0.03)
        session.finish_session(mesh)
        self.assertNotIn("RenamedBezierControls", bpy.data.objects)
        self.assertEqual(bpy.data.objects.get(old_curve_name), unrelated_curve)
        self.assertEqual(bpy.data.objects.get("Character"), unrelated_mesh)
        self.assertCoordinatesEqual(key_coordinates(unrelated_mesh, preview_name), unrelated_coordinates)
        self.assertIn("RenamedSessionPose", mesh.data.shape_keys.key_blocks)

    def test_invalid_selection_is_rejected_without_data_mutations(self):
        for kind in ("empty", "open", "branch", "two_loops"):
            with self.subTest(selection=kind):
                mesh, n, _ = fixture(name="Invalid_" + kind)
                if kind == "empty":
                    predicate = lambda a, b: False
                elif kind == "open":
                    predicate = lambda a, b: a < 7 and b < 7
                elif kind == "branch":
                    predicate = lambda a, b: (a < n and b < n) or {a, b} == {0, n}
                else:
                    predicate = lambda a, b: (a < n and b < n) or (n <= a < 2 * n and n <= b < 2 * n)
                select_edges(mesh, predicate)
                before_objects = {obj.as_pointer() for obj in bpy.data.objects}
                before_curves = {curve.as_pointer() for curve in bpy.data.curves}
                before_keys = {key.as_pointer() for key in bpy.data.shape_keys}
                with self.assertRaises((ValueError, RuntimeError)):
                    session.create_session(bpy.context, 8, 0.3, 2.0)
                self.assertEqual(before_objects, {obj.as_pointer() for obj in bpy.data.objects})
                self.assertEqual(before_curves, {curve.as_pointer() for curve in bpy.data.curves})
                self.assertEqual(before_keys, {key.as_pointer() for key in bpy.data.shape_keys})
                self.assertFalse(mesh.curvemorph.token)
                self.assertIsNone(mesh.data.shape_keys)

    def test_active_existing_shape_key_is_rejected_without_changes(self):
        mesh, n, _ = fixture()
        mesh.shape_key_add(name="Basis")
        smile = mesh.shape_key_add(name="ActiveSmile")
        smile.data[0].co.z = 0.3
        smile.value = 0.65
        select_loop(mesh, n)
        before_objects = {obj.as_pointer() for obj in bpy.data.objects}
        before = coordinates(smile.data)
        with self.assertRaises((ValueError, RuntimeError)):
            session.create_session(bpy.context, 8, 0.3, 2.0)
        self.assertEqual(before_objects, {obj.as_pointer() for obj in bpy.data.objects})
        self.assertCoordinatesEqual(coordinates(smile.data), before)
        self.assertAlmostEqual(smile.value, 0.65, places=5)
        self.assertEqual(len(mesh.data.shape_keys.key_blocks), 2)
        self.assertFalse(mesh.curvemorph.token)

    def test_shared_mesh_is_rejected_without_changes(self):
        mesh, n, _ = fixture()
        linked = bpy.data.objects.new("LinkedCharacter", mesh.data)
        bpy.context.collection.objects.link(linked)
        select_loop(mesh, n)
        before_objects = {obj.as_pointer() for obj in bpy.data.objects}
        with self.assertRaises((ValueError, RuntimeError)):
            session.create_session(bpy.context, 8, 0.3, 2.0)
        self.assertEqual(before_objects, {obj.as_pointer() for obj in bpy.data.objects})
        self.assertEqual(mesh.data, linked.data)
        self.assertIsNone(mesh.data.shape_keys)

    def test_session_survives_save_and_reload(self):
        mesh, n, _ = fixture(name="SavedCharacter")
        self.create(mesh, n)
        edit_one_control(mesh)
        preview_name = mesh.curvemorph.preview_name
        before_reload = key_coordinates(mesh, preview_name)
        saved_token = mesh.curvemorph.token
        activate(mesh)
        with tempfile.TemporaryDirectory(prefix="curvemorph_test_") as directory:
            path = str(Path(directory) / "session.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
            bpy.ops.wm.open_mainfile(filepath=path)
            restored = bpy.data.objects["SavedCharacter"]
            self.assertEqual(restored.curvemorph.token, saved_token)
            self.assertIsNotNone(restored.curvemorph.curve)
            session.update_session(restored, force=True)
            self.assertCoordinatesEqual(key_coordinates(restored, preview_name), before_reload)
            edit_one_control(restored, translation=(0.0, 0.0, 0.15))
            self.assertGreater(max_difference(key_coordinates(restored, preview_name), before_reload), 0.02)
            saved = session.save_shape_key(restored, "AfterReload", reset_after=True)
            self.assertEqual(saved.name, "AfterReload")
            session.finish_session(restored)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CurveMorphIntegrationTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"CURVEMORPH_INTEGRATION: {result.testsRun} tests; "
          f"failures={len(result.failures)} errors={len(result.errors)}", flush=True)
    if not result.wasSuccessful():
        raise SystemExit(1)
