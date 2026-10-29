import unittest

from scripts.package_common_native_checkpoint import SPEAKER_OPERATIONS, enrollment_interfaces

READS = {'speaker.list', 'speaker.buckets', 'speaker.recovery_list', 'recording.record', 'recording.status',
         'recording.list', 'vocabulary.list'}


def descriptors(speaker=SPEAKER_OPERATIONS):
    ids = ['speech.session.' + str(i) for i in range(8)] + sorted(speaker)
    ids += ['recording.' + n for n in ('record', 'status', 'list', 'delete', 'prune')]
    ids += ['vocabulary.' + n for n in ('list', 'correct', 'forget')]
    return [{'id': i, 'operator_confirms': i.split('.')[0] in ('speaker', 'vocabulary', 'recording') and i not in READS}
            for i in ids]


class EnrollmentInterfaces(unittest.TestCase):
    def test_the_recovery_operations_are_speaker_operations(self):
        got = {i['id']: i['methods'] for i in enrollment_interfaces(descriptors())}
        self.assertIn('speaker.recovery_list', got['speaker.uid'])
        self.assertIn('speaker.recovery_delete', got['speaker.uid'])
        self.assertEqual(len(got['speaker.uid']), 12)

    def test_a_set_without_them_is_incomplete(self):
        with self.assertRaisesRegex(ValueError, 'incomplete guided enrollment interface'):
            enrollment_interfaces(descriptors(SPEAKER_OPERATIONS - {'speaker.recovery_list', 'speaker.recovery_delete'}))

    def test_the_delete_asks_and_the_list_does_not(self):
        for name, confirms in (('speaker.recovery_delete', False), ('speaker.recovery_list', True)):
            ds = descriptors()
            next(d for d in ds if d['id'] == name)['operator_confirms'] = confirms
            with self.assertRaisesRegex(ValueError, 'speaker confirmation declaration differs'):
                enrollment_interfaces(ds)


if __name__ == '__main__':
    unittest.main()
