import unittest
from scripts.run_speaker_aware_reference import group_segments


class SpeakerAwareReferenceTest(unittest.TestCase):
    def setUp(self):
        self.panel = {"cases": [{"id": "a", "samples": 16000}, {"id": "b", "samples": 16000}]}
        self.row = dict(session_id="a", speaker="0", start_time=.1, end_time=.9, words="hello")

    def test_missing_speech_is_empty_not_missing_case(self):
        got = group_segments(self.panel, [self.row])
        self.assertEqual(got, {"a": [self.row], "b": []})

    def test_unknown_session_is_never_silently_ignored(self):
        with self.assertRaises(KeyError):
            group_segments(self.panel, [{**self.row, "session_id": "extra"}])

    def test_timestamp_overshoot_is_not_clipped(self):
        with self.assertRaisesRegex(ValueError, "source clock"):
            group_segments(self.panel, [{**self.row, "end_time": 1.081}])
        row = {**self.row, "end_time": 1.079}
        self.assertEqual(group_segments(self.panel, [row])["a"][0]["end_time"], 1.079)

    def test_duplicate_case_is_refused(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            group_segments({"cases": self.panel["cases"] * 2}, [])


if __name__ == "__main__":
    unittest.main()
