import pytest

from scripts.report_native_voice_latency import distribution, elapsed, measurements


def row(kind, timestamp, **fields):
    return {"type": kind, "observed_monotonic_ns": timestamp * 1000000, **fields}


def test_metrics_link_the_actual_turn_and_native_stop_not_generation_finish():
    events = [
        row("speech_start", 110, sequence=1, start_sample=0, end_sample=512),
        row("transcript_partial", 200, sequence=2, start_sample=0, end_sample=512),
        row("transcript_final", 300, sequence=3, start_sample=0, end_sample=1024),
        row("turn_committed", 310, sequence=4),
        row("synthesis_start", 320, sequence=5, synthesis_id="s1"),
        row("audio_chunk", 500, sequence=6, synthesis_id="s1"),
        row(
            "interruption_requested",
            700,
            sequence=7,
            synthesis_id="s1",
            reason="native_vad_speech",
        ),
    ]
    journal = [
        row("fast_vad", 100, start_sample=0, end_sample=512, probability=0.9),
        row(
            "native_playback",
            520,
            native={"type": "playback_start", "synthesis_id": "s1"},
        ),
        row(
            "native_playback",
            705,
            native={
                "type": "playback_stop",
                "synthesis_id": "s1",
                "reason": "control_cancel",
                "submitted_samples": 1000,
                "completed_samples": 500,
            },
        ),
    ]
    result = measurements(events, journal)
    assert result["positive_vad_to_first_partial_ms"] == [100]
    assert result["turn_commit_to_first_generated_audio_ms"] == [190]
    assert result["first_generated_audio_to_playback_start_observed_ms"] == [20]
    assert result["last_positive_vad_to_playback_start_observed_ms"] == [420]
    assert result["interruption_request_to_playback_stop_observed_ms"] == [5]
    journal[-1]["native"]["completed_samples"] = 1000
    assert (
        measurements(events, journal)[
            "interruption_request_to_playback_stop_observed_ms"
        ]
        == []
    )


def test_missing_observation_is_not_zero_latency_and_clock_inversion_fails():
    assert distribution([])["median"] is None
    assert all(values == [] for values in measurements([], []).values())
    with pytest.raises(ValueError, match="causally ordered"):
        elapsed(row("a", 10), row("b", 5))
