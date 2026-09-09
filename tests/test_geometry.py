"""Run with: python -m unittest discover -s tests -p test_geometry.py."""

import importlib.util
from pathlib import Path
import unittest

import numpy as np
from numpy.testing import assert_allclose


_spec = importlib.util.spec_from_file_location(
    "mouth_geometry", Path(__file__).resolve().parents[1] / "geometry.py"
)
geometry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(geometry)


def square_surface():
    """Two connected square rings plus a disconnected vertex nearby."""
    vertices = np.array(
        [
            (-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0),
            (-2, -2, 0), (2, -2, 0), (2, 2, 0), (-2, 2, 0),
            (-1, -1, 0.001),
        ],
        dtype=float,
    )
    edges = [(0, 1), (1, 2), (2, 3), (3, 0)]
    edges += [(4, 5), (5, 6), (6, 7), (7, 4)]
    edges += [(0, 4), (1, 5), (2, 6), (3, 7)]
    return vertices, edges, [0, 1, 2, 3]


class LoopTests(unittest.TestCase):
    def test_order_is_connected_and_deterministic(self):
        self.assertEqual(
            geometry.order_closed_loop(8, [(5, 3), (1, 5), (7, 1), (3, 7)]),
            [1, 5, 3, 7],
        )

    def test_reject_open_branched_disconnected_and_small_loops(self):
        invalid = [
            [(0, 1), (1, 2), (2, 3)],
            [(0, 1), (1, 2), (2, 3), (3, 0), (0, 2)],
            [(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3)],
            [(0, 1), (1, 2), (2, 0)],
            [(0, 1), (1, 2), (2, 3), (3, 0), (1, 0)],
            [(0, 1), (1, 2), (2, 3), (3, 8)],
        ]
        for edges in invalid:
            with self.subTest(edges=edges), self.assertRaises(ValueError):
                geometry.order_closed_loop(8, edges)

    def test_arc_fractions_and_control_sampling(self):
        points = np.array([(0, 0, 0), (3, 0, 0), (3, 1, 0), (0, 1, 0)])
        parameters, perimeter = geometry.cyclic_parameters(points)
        assert_allclose(parameters, [0, 3 / 8, 4 / 8, 7 / 8])
        self.assertEqual(perimeter, 8)
        assert_allclose(
            geometry.resample_loop(points, 4),
            [(0, 0, 0), (2, 0, 0), (3, 1, 0), (1, 1, 0)],
        )

    def test_repeated_points_and_tiny_geometry(self):
        points = np.array([(0, 0, 0), (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
        expected = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
        assert_allclose(geometry.resample_loop(points, 4), expected)
        assert_allclose(geometry.resample_loop(points * 1e-12, 4) / 1e-12, expected)
        with self.assertRaises(ValueError):
            geometry.resample_loop(np.zeros((4, 3)), 4)

    def test_trailing_coincident_point_stays_in_half_open_parameter_interval(self):
        points = np.array([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 0)])
        parameters, _ = geometry.cyclic_parameters(points)
        self.assertTrue(np.all(parameters >= 0))
        self.assertTrue(np.all(parameters < 1))
        assert_allclose(geometry.resample_loop(points, 4), points[:4])


class BezierTests(unittest.TestCase):
    def setUp(self):
        self.co = np.array([(1, 0, 0), (0, 1, 0), (-1, 0, 0), (0, -1, 0)], dtype=float)
        tangent = np.array([(0, 1, 0), (-1, 0, 0), (0, -1, 0), (1, 0, 0)]) / 3
        self.left = self.co - tangent
        self.right = self.co + tangent

    def test_control_points_and_cyclic_seam(self):
        assert_allclose(
            geometry.evaluate_bezier(self.co, self.left, self.right, [0, 0.25, 0.5, 0.75, 1]),
            np.vstack((self.co, self.co[:1])),
        )
        assert_allclose(
            geometry.evaluate_bezier(self.co, self.left, self.right, [-1e-30]),
            self.co[:1],
        )

    def test_position_and_tangent_are_continuous_at_joins(self):
        step = 1e-7
        for parameter in [0, 0.25, 0.5, 0.75]:
            before, at, after = geometry.evaluate_bezier(
                self.co, self.left, self.right, [parameter - step, parameter, parameter + step]
            )
            assert_allclose(before, after, atol=1e-6)
            assert_allclose((at - before) / step, (after - at) / step, atol=2e-5)

    def test_uniform_control_translation_and_zero_rest_delta(self):
        parameters = np.linspace(0, 1, 101)
        rest = geometry.evaluate_bezier(self.co, self.left, self.right, parameters)
        assert_allclose(rest - rest, 0)
        movement = np.array([0.13, -0.5, 2.0])
        posed = geometry.evaluate_bezier(
            self.co + movement, self.left + movement, self.right + movement, parameters
        )
        assert_allclose(posed - rest, np.tile(movement, (len(parameters), 1)), atol=1e-14)


class BindingTests(unittest.TestCase):
    def test_identity_is_exact(self):
        binding = geometry.build_binding(*square_surface(), radius=3.0)
        assert_allclose(geometry.apply_binding(binding, np.zeros((4, 3)), 3), 0, atol=0)

    def test_loop_vertices_interpolate_exactly(self):
        binding = geometry.build_binding(*square_surface(), radius=3.0)
        movement = np.arange(12).reshape((4, 3)) / 10
        result = geometry.apply_binding(binding, movement, 3, falloff=2.5)
        for loop_index in range(4):
            row = np.flatnonzero(binding["indices"] == loop_index)[0]
            assert_allclose(result[row], movement[loop_index], atol=0)

    def test_radius_and_disconnected_geometry(self):
        binding = geometry.build_binding(*square_surface(), radius=1.0)
        self.assertEqual(binding["indices"].tolist(), [0, 1, 2, 3])
        binding = geometry.build_binding(*square_surface(), radius=3.0)
        self.assertEqual(binding["indices"].tolist(), list(range(8)))
        assert_allclose(binding["distances"], [0, 0, 0, 0] + [2**0.5] * 4)
        narrower = geometry.apply_binding(binding, np.ones((4, 3)), 1.0)
        assert_allclose(narrower[4:], 0, atol=0)

    def test_weights_preserve_uniform_translation_times_fade(self):
        binding = geometry.build_binding(*square_surface(), radius=3.0)
        assert_allclose(binding["weights"].sum(axis=1), 1.0)
        movement = np.array([1.0, -2.0, 0.25])
        result = geometry.apply_binding(binding, np.tile(movement, (4, 1)), 3)
        distance = binding["distances"] / 3
        fade = (1 - distance) ** 2 * (1 + 2 * distance)
        assert_allclose(result, movement * fade[:, None])

    def test_zero_radius_only_moves_loop_even_with_zero_length_edges(self):
        vertices, edges, loop = square_surface()
        vertices[4] = vertices[0]
        binding = geometry.build_binding(vertices, edges, loop, 3)
        result = geometry.apply_binding(binding, np.ones((4, 3)), 0)
        assert_allclose(result[:4], 1)
        assert_allclose(result[4:], 0)
        zero_binding = geometry.build_binding(vertices, edges, loop, 0)
        self.assertEqual(zero_binding["indices"].tolist(), [0, 1, 2, 3])

    def test_distance_follows_surface_path_not_spatial_proximity(self):
        vertices, edges, loop = square_surface()
        vertices = np.vstack((vertices, [(-1, -1, 0.002)]))
        edges.append((4, 9))
        binding = geometry.build_binding(vertices, edges, loop, 2)
        self.assertNotIn(9, binding["indices"])

    def test_more_falloff_reduces_only_surrounding_mesh_motion(self):
        binding = geometry.build_binding(*square_surface(), radius=3)
        broad = geometry.apply_binding(binding, np.ones((4, 3)), 3, 1)
        narrow = geometry.apply_binding(binding, np.ones((4, 3)), 3, 2)
        assert_allclose(broad[:4], narrow[:4])
        self.assertTrue(np.all(narrow[4:] < broad[4:]))

    def test_reject_invalid_radius_and_falloff(self):
        for radius in [-1, float("nan"), float("inf")]:
            with self.subTest(radius=radius), self.assertRaises(ValueError):
                geometry.build_binding(*square_surface(), radius=radius)
        binding = geometry.build_binding(*square_surface(), radius=3)
        for falloff in [-1, 0, float("nan")]:
            with self.subTest(falloff=falloff), self.assertRaises(ValueError):
                geometry.apply_binding(binding, np.ones((4, 3)), 3, falloff)


if __name__ == "__main__":
    unittest.main()
