"""The endurance verdict must cover the whole requested span, not its first turn."""
import pytest

from scripts.prove_native_meeting_endurance import validate_observations


def test_one_early_final_cannot_certify_later_dead_recognition():
    with pytest.raises(AssertionError):
        validate_observations([{'sequence': 1, 'start_sample': 0}],
                              [{'refers_to': 1}], 120, 10, 60)


def test_late_transcript_needs_its_own_speaker_observation():
    finals = [{'sequence': 1, 'start_sample': 0}, {'sequence': 2, 'start_sample': 60}]
    with pytest.raises(AssertionError):
        validate_observations(finals, [{'refers_to': 1}], 120, 10, 60)
    validate_observations(finals, [{'refers_to': 1}, {'refers_to': 2}], 120, 10, 60)


def test_final_beyond_recording_is_not_a_replacement_for_lost_speech():
    with pytest.raises(AssertionError):
        validate_observations([{'sequence': 1, 'start_sample': 30}],
                              [{'refers_to': 1}], 60, 10, 60)
