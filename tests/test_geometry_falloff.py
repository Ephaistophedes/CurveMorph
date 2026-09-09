"""Falloff profile formulas, boundaries, and stable random distribution."""
import unittest
import numpy as np
from numpy.testing import assert_allclose, assert_array_equal
from test_geometry import geometry


class FalloffTests(unittest.TestCase):
    def binding(self):
        return {'indices': np.array([7, 11, 13, 17, 19, 23]),
                'distances': np.array([0., .25, .5, .99, 1., 1.5]),
                'loop_mask': np.array([True, False, False, False, False, False])}

    def test_all_named_profiles_match_expected_quarter_radius_values(self):
        expected = {'SMOOTH': .84375, 'SPHERE': np.sqrt(.9375), 'ROOT': np.sqrt(.75),
                    'INVERSE_SQUARE': .9375, 'SHARP': .5625, 'LINEAR': .75, 'CONSTANT': 1}
        for name, value in expected.items():
            with self.subTest(profile=name):
                fade = geometry.binding_falloff(self.binding(), 1, falloff_type=name)
                self.assertAlmostEqual(fade[1], value)
                self.assertEqual(fade[0], 1)
                assert_array_equal(fade[-2:], 0)
                self.assertTrue(np.all(np.diff(fade) <= 0))

    def test_strength_and_zero_radius_for_every_profile(self):
        for name in geometry.FALLOFF_TYPES:
            base = geometry.binding_falloff(self.binding(), 1, falloff_type=name)
            assert_allclose(geometry.binding_falloff(self.binding(), 1, 2, name), base**2)
            assert_array_equal(geometry.binding_falloff(self.binding(), 0, falloff_type=name), [1, 0, 0, 0, 0, 0])

    def test_random_is_repeatable_by_vertex_id_not_row_order(self):
        binding = self.binding()
        original = geometry.binding_falloff(binding, 1, falloff_type='RANDOM', random_seed=17)
        subset = {k: v[[3, 1, 2]] for k, v in binding.items()}
        assert_array_equal(geometry.binding_falloff(subset, 1, falloff_type='RANDOM', random_seed=17), original[[3, 1, 2]])
        other = geometry.binding_falloff(binding, 1, falloff_type='RANDOM', random_seed=18)
        self.assertGreater(np.max(abs(original-other)), .01)
        self.assertTrue(np.all(original[1:] <= 1-np.minimum(binding['distances'][1:], 1)))
        self.assertTrue(np.isfinite(geometry.binding_falloff(binding, 1, falloff_type='RANDOM', random_seed=2147483647)).all())

    def test_unknown_profile_rejected(self):
        with self.assertRaises(ValueError):
            geometry.binding_falloff(self.binding(), 1, falloff_type='UNKNOWN')


if __name__ == '__main__':
    unittest.main()
