"""Numerical tilt interpolation and rotation checks, independent of Blender."""
import unittest
import numpy as np
from numpy.testing import assert_allclose
from test_geometry import geometry


class TiltGeometryTests(unittest.TestCase):
    def test_constant_tilt_all_interpolation_modes_and_cyclic_seam(self):
        parameters = np.linspace(-.1, 1.1, 21)
        for mode in ('LINEAR', 'EASE', 'CARDINAL', 'BSPLINE'):
            assert_allclose(geometry.interpolate_tilt([.7]*4, parameters, mode), .7, atol=1e-12)
            self.assertAlmostEqual(geometry.interpolate_tilt([.1, .2, .3, .4], [0, 1], mode)[0],
                                   geometry.interpolate_tilt([.1, .2, .3, .4], [0, 1], mode)[1])

    def test_interpolation_values_and_full_turns(self):
        values = [0, 2, 0, 0]
        self.assertAlmostEqual(geometry.interpolate_tilt(values, [.0625], 'LINEAR')[0], .5)
        self.assertAlmostEqual(geometry.interpolate_tilt(values, [.0625], 'EASE')[0], .3125)
        self.assertAlmostEqual(geometry.interpolate_tilt([0, 4*np.pi, 0, 0], [.125])[0], 2*np.pi)
        with self.assertRaises(ValueError):
            geometry.interpolate_tilt([np.nan], [0])

    def test_quarter_turn_rotation_and_radius_fade(self):
        binding = {'sources': np.array([[0], [0]]), 'weights': np.ones((2, 1)),
                   'distances': np.array([.5, 0]), 'loop_mask': np.array([False, True])}
        offsets = np.array([[[1., 0, 0]], [[0, 0, 0]]])
        result = geometry.apply_tilt(binding, offsets, np.array([[0., 1, 0]]), np.array([np.pi/2]), 1)
        assert_allclose(result, [[-.5, 0, -.5], [0, 0, 0]], atol=1e-12)
        assert_allclose(geometry.apply_tilt(binding, offsets, np.zeros((1, 3)), np.array([1.]), 1), 0)

    def test_tangents_fallback_for_zero_handles_and_collapsed_curve(self):
        co = np.array([[0., 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]])
        tangents = geometry.bezier_tangents(co, co, co, np.array([0, .25, .5, .75]))
        assert_allclose(tangents, [[1, 0, 0], [0, 1, 0], [-1, 0, 0], [0, -1, 0]])
        assert_allclose(geometry.bezier_tangents(co*0, co*0, co*0, np.array([0.])), 0)


if __name__ == '__main__':
    unittest.main()
