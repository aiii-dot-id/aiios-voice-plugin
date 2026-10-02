import copy
import unittest

from scripts.prove_native_separator import validate


class NativeSeparatorGateTests(unittest.TestCase):
    def fixture(self):
        rows, events = [], []
        for case, n in enumerate((32000, 72000)):
            for phase in range(3):
                row = {'case': case}
                if phase == 1:
                    row.update(cancelled=True, cancel_requested_after_ms=25,
                               cancel_admission_ms=.02, cancel_retirement_ms=2)
                else:
                    row.update(repeat=phase//2, samples=n, relative_l2=1e-6,
                               inference_seconds=.5, runtime_version='test-runtime')
                rows.append(row)
                ts = (case*3+phase)*1000
                events += [{'name': 'model_run', 'ts': ts, 'dur': 500},
                           {'name': 'kernel', 'ts': ts+1, 'dur': 20,
                            'args': {'provider': 'CUDAExecutionProvider'}}]
        return rows, events

    def test_complete_active_cancel_and_recovery(self):
        rows, events = self.fixture()
        proof = validate(rows, events, [32000, 72000], 'cuda', 0)
        self.assertEqual(len(proof), 6)
        self.assertEqual(proof[1]['phase'], 'cancel')

    def test_no_pre_cancelled_or_fallback_pass(self):
        rows, events = self.fixture()
        for idx in (1, 3, 5, 7, 9, 11):
            with self.subTest(kernel=idx):
                changed = copy.deepcopy(events)
                changed[idx]['args']['provider'] = 'CPUExecutionProvider'
                with self.assertRaises(ValueError):
                    validate(rows, changed, [32000, 72000], 'cuda', 0)

    def test_missing_profile_and_nonzero_exit_fail(self):
        rows, events = self.fixture()
        for changed, code in ((events[:-2], 0), (events, 1)):
            with self.assertRaises(ValueError):
                validate(rows, changed, [32000, 72000], 'cuda', code)

    def test_bad_cancellation_rejected(self):
        for key, value in [('cancelled', False), ('cancel_requested_after_ms', 0),
                           ('cancel_admission_ms', 51), ('cancel_retirement_ms', 251),
                           ('cancel_retirement_ms', -1), ('cancel_retirement_ms', float('nan'))]:
            with self.subTest(key=key, value=value):
                rows, events = self.fixture(); rows[1][key] = value
                with self.assertRaises(ValueError):
                    validate(rows, events, [32000, 72000], 'cuda', 0)

    def test_recovery_must_be_exact_case_and_reference(self):
        for key, value in [('case', 1), ('samples', 32001), ('repeat', 0),
                           ('relative_l2', .001), ('relative_l2', float('nan')),
                           ('runtime_version', 'changed')]:
            with self.subTest(key=key, value=value):
                rows, events = self.fixture(); rows[2][key] = value
                with self.assertRaises(ValueError):
                    validate(rows, events, [32000, 72000], 'cuda', 0)

    def test_reordering_or_missing_recovery_fails(self):
        rows, events = self.fixture()
        for changed in (rows[:-1], [rows[2], rows[1], rows[0]]+rows[3:]):
            with self.assertRaises(ValueError):
                validate(changed, events, [32000, 72000], 'cuda', 0)

    def test_requested_threads_must_match_all_completed_calls(self):
        rows, events = self.fixture()
        for row in rows:
            if 'repeat' in row: row['intra_op_threads'] = 12
        self.assertEqual(len(validate(rows, events, [32000, 72000], 'cuda', 0, 12)), 6)
        rows[2]['intra_op_threads'] = 4
        with self.assertRaises(ValueError):
            validate(rows, events, [32000, 72000], 'cuda', 0, 12)


if __name__ == '__main__':
    unittest.main()
