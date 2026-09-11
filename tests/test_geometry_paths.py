import importlib.util
from pathlib import Path
import unittest
import numpy as np

spec = importlib.util.spec_from_file_location('geometry', Path(__file__).resolve().parents[1] / 'geometry.py')
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


class OpenPathTests(unittest.TestCase):
    def test_order_and_invalid_selections(self):
        self.assertEqual(g.order_path(4, [(2, 3), (1, 2), (0, 1)]), ([0, 1, 2, 3], False))
        self.assertTrue(g.order_path(4, [(0, 1), (1, 2), (2, 3), (3, 0)])[1])
        for edges in ([], [(0, 1), (1, 2), (1, 3)], [(0, 1), (2, 3)], [(0, 1), (0, 1)],
                      [(0, 1), (2, 3), (3, 4), (4, 2)]):
            with self.assertRaises(ValueError):
                g.order_path(5, edges)

    def test_endpoints_do_not_wrap(self):
        points = np.array([[0., 0, 0], [1, 0, 0], [4, 0, 0]])
        co, params = g.path_layout(points, 3)
        np.testing.assert_allclose(params, [0, .25, 1])
        left, right = co - [2/3, 0, 0], co + [2/3, 0, 0]
        out = g.evaluate_bezier(co, left, right, [-1, 0, .5, 1, 2], closed=False)
        np.testing.assert_allclose(out[:, 0], [0, 0, 2, 4, 4])
        np.testing.assert_allclose(g.bezier_tangents(co, left, right, [0, 1], closed=False), [[1, 0, 0], [1, 0, 0]])
        np.testing.assert_allclose(g.interpolate_tilt([0, 1, 2], [0, .5, 1], closed=False), [0, 1, 2])

    def test_two_seed_open_binding_and_island(self):
        points = np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, .01]])
        binding = g.build_binding(points, [(0, 1), (0, 2)], [0, 1], 2, closed=False)
        self.assertNotIn(3, binding['indices'])
        moved = g.apply_binding(binding, [[0, 0, 1], [0, 0, 2]], 2)
        np.testing.assert_allclose(moved[:2, 2], [1, 2])


if __name__ == '__main__':
    unittest.main()
