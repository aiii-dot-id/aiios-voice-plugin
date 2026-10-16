import copy
import unittest
from scripts.score_speaker_aware import evaluate, score


def row(speaker, text, start=0):
    return dict(speaker=speaker, words=text, start_time=start, end_time=start+1)


class SpeakerAwareScoreTest(unittest.TestCase):
    def setUp(self):
        self.ref = [row("A", "red lantern seven"), row("B", "blue garden nine")]

    def test_anonymous_permutation_is_valid(self):
        self.assertEqual(score(self.ref, [row("x", "blue garden nine"), row("y", "red lantern seven")])["cpwer"], 0)

    def test_collapsed_mixture_cannot_pass(self):
        s = score(self.ref, [row("enrolled_speaker", "red lantern seven blue garden nine")])
        self.assertGreater(s["cpwer"], .25)
        self.assertEqual(s["hypothesis_speakers"], 1)

    def test_dropping_second_speaker_cannot_pass(self):
        self.assertEqual(score(self.ref, [row("A", "red lantern seven")])["cpwer"], .5)

    def test_tracks_cannot_be_reassigned_per_segment(self):
        ref = self.ref + [row("A", "white cloud ten", 3), row("B", "green field six", 3)]
        hyp = self.ref + [row("B", "white cloud ten", 3), row("A", "green field six", 3)]
        self.assertGreater(score(ref, hyp)["cpwer"], .25)

    def test_extra_track_and_empty_output_are_errors(self):
        self.assertGreater(score(self.ref, self.ref+[row("C", "invented words")])["cpwer"], 0)
        self.assertEqual(score(self.ref, [])["cpwer"], 1)

    def test_complete_census(self):
        panel = dict(cases=[dict(id="x", reference=self.ref)], gate=dict(max_case_cpwer=.25, max_speaker_wer=.35))
        self.assertTrue(evaluate(panel, {"x": self.ref})["passed"])
        for hyp in ({}, {"x":self.ref, "extra":self.ref}):
            with self.assertRaises(ValueError): evaluate(panel, hyp)
        broken = copy.deepcopy(panel); broken["cases"] *= 2
        with self.assertRaises(ValueError): evaluate(broken, {"x":self.ref})

    def test_invalid_metadata(self):
        for value in (float("nan"), -1):
            bad = row("a", "text", value)
            with self.assertRaises(ValueError): score(self.ref, [bad])
        with self.assertRaises(ValueError): score(self.ref, [row(None, "text")])


if __name__ == "__main__":
    unittest.main()
