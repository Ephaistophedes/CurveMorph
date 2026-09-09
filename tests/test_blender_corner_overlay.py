"""Corner guide regression checks without requiring a GPU in background mode."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from test_blender_integration import *
from test_blender_setup import stored_setup

overlay = addon.corner_overlay


class CornerOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        addon.register()

    @classmethod
    def tearDownClass(cls):
        session._object_mode()
        addon.unregister()

    def setUp(self):
        session._object_mode()
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)

    def test_first_corner_selection_highlights_loop_without_selecting_it(self):
        mesh, n, _ = fixture()
        select_loop(mesh, n)
        addon.setup.capture_loop(bpy.context)
        addon.setup.begin_edit(bpy.context, mesh, 'CORNERS')
        bm = bmesh.from_edit_mesh(mesh.data)
        bm.verts.ensure_lookup_table()
        self.assertEqual(sum(v.select for v in bm.verts), 0)
        before = coordinates(bm.verts)
        self.assertEqual(len(overlay.loop_segments(bpy.context)), 2*n)
        bm.verts[0].select_set(True)
        bm.verts[n//2].select_set(True)
        bmesh.update_edit_mesh(mesh.data)
        self.assertEqual(len(overlay.loop_segments(bpy.context)), 2*n)
        self.assertEqual(sum(v.select for v in bm.verts), 2)
        self.assertEqual(coordinates(bm.verts), before)
        self.assertIsNone(mesh.data.shape_keys)
        self.assertEqual(len(mesh.vertex_groups), 0)
        self.assertEqual(len(bpy.data.objects), 1)
        addon.setup.capture_corners(bpy.context)
        self.assertEqual(mesh.curvemorph.setup_kind, 'NONE')
        self.assertEqual(overlay.loop_segments(bpy.context), [])

    def test_revisit_restores_only_corners_and_unhides_loop(self):
        mesh, n, _ = fixture()
        stored_setup(mesh, n)
        addon.setup.begin_edit(bpy.context, mesh, 'LOOP')
        bm = bmesh.from_edit_mesh(mesh.data)
        for edge in bm.edges:
            if edge.select:
                edge.hide_set(True)
        bmesh.update_edit_mesh(mesh.data)
        addon.setup.begin_edit(bpy.context, mesh, 'CORNERS')
        bm = bmesh.from_edit_mesh(mesh.data)
        self.assertEqual([v.index for v in bm.verts if v.select], [0, n//2])
        self.assertTrue(all(not v.hide for v in bm.verts if v.index < n))
        self.assertEqual(len(overlay.loop_segments(bpy.context)), 2*n)

    def test_world_transform_and_visibility_scope(self):
        mesh, n, _ = fixture()
        stored_setup(mesh, n)
        mesh.location = (3, -2, 5)
        mesh.rotation_euler = (.4, .2, .8)
        mesh.scale = (2, .8, 1.2)
        bpy.context.view_layer.update()
        addon.setup.begin_edit(bpy.context, mesh, 'CORNERS')
        bm = bmesh.from_edit_mesh(mesh.data)
        bm.verts.ensure_lookup_table()
        loop = list(addon.setup.read(mesh)['loop'])
        a = mesh.matrix_world @ bm.verts[loop[0]].co
        b = mesh.matrix_world @ bm.verts[loop[1]].co
        self.assertLess((Vector(overlay.loop_segments(bpy.context)[0]) - a.lerp(b, .06)).length, 1e-6)
        session._object_mode()
        self.assertEqual(overlay.loop_segments(bpy.context), [])
        addon.setup.begin_edit(bpy.context, mesh, 'MASK')
        self.assertEqual(overlay.loop_segments(bpy.context), [])
        addon.setup.begin_edit(bpy.context, mesh, 'LOOP')
        self.assertEqual(overlay.loop_segments(bpy.context), [])

    def test_changed_topology_never_highlights_stale_indices(self):
        mesh, n, _ = fixture()
        stored_setup(mesh, n)
        addon.setup.begin_edit(bpy.context, mesh, 'CORNERS')
        bm = bmesh.from_edit_mesh(mesh.data)
        bm.verts.new((0, 0, 0))
        bmesh.update_edit_mesh(mesh.data)
        self.assertEqual(overlay.loop_segments(bpy.context), [])


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CornerOverlayTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
