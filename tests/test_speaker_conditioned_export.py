import unittest
import numpy as np

from scripts.export_speaker_conditioned_encoder import (
    INPUT_NAMES, compare_outputs, require_inputs, require_target_effect,
)


class SpeakerConditionedExportTest(unittest.TestCase):
    def fixtures(self):
        return [np.zeros((1, 2, 3), np.float32), np.array([2], np.int64),
                np.zeros((1, 2, 3), np.float32), np.zeros((1, 2, 3), np.float32),
                np.array([2], np.int64)]

    def test_both_speaker_inputs_must_survive_export(self):
        require_inputs(INPUT_NAMES)
        for missing in ('speaker_targets', 'background_targets', 'cache_last_time'):
            with self.subTest(missing=missing), self.assertRaises(AssertionError):
                require_inputs(n for n in INPUT_NAMES if n != missing)

    def test_exact_geometry_and_dtype_not_broadcasting(self):
        expected = self.fixtures()
        self.assertEqual(len(compare_outputs(self.fixtures(), expected, 'valid')), 5)
        for bad in (np.zeros((1, 1, 3), np.float32), np.zeros((1, 2, 3), np.float64)):
            got = self.fixtures(); got[0] = bad
            with self.assertRaisesRegex(AssertionError, 'shape or dtype'):
                compare_outputs(got, expected, 'bad')

    def test_output_census_and_integer_cache_length_are_exact(self):
        expected = self.fixtures()
        with self.assertRaisesRegex(AssertionError, 'count'):
            compare_outputs(expected[:-1], expected, 'missing')
        got = self.fixtures(); got[4][0] += 1
        with self.assertRaises(AssertionError):
            compare_outputs(got, expected, 'cache')

    def test_nan_is_never_equal_to_nan(self):
        got, expected = self.fixtures(), self.fixtures()
        got[0][0, 0, 0] = expected[0][0, 0, 0] = np.nan
        with self.assertRaisesRegex(AssertionError, 'nonfinite'):
            compare_outputs(got, expected, 'nan')

    def test_unconditioned_export_and_bad_cache_are_killed(self):
        values = np.ones((1, 2, 3), np.float32)
        with self.assertRaisesRegex(AssertionError, 'ignores'):
            require_target_effect(values, values.copy())
        self.assertGreater(require_target_effect(values, values * .5), .4)
        expected = self.fixtures(); got = self.fixtures(); got[2] += 1
        with self.assertRaises(AssertionError):
            compare_outputs(got, expected, 'wrong-owner-cache')


if __name__ == '__main__':
    unittest.main()
