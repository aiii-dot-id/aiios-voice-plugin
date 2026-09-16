"""Summary gates only; full evidence validation runs before this function."""

import json

import pytest

from scripts.assess_native_soak import summarize


@pytest.fixture
def fixture(tmp_path):
    report = {
        "settings": {"max_session_seconds": 8},
        "identity": {"source_sha256": "a" * 64},
        "native_binding": {"binary_sha256": "b" * 64},
        "native_terminal": {
            "reason": "finished",
            "capture_drops": 0,
            "render_drops": 0,
        },
    }
    journal = [
        {"type": "capture_ready", "observed_monotonic_ns": 1_000_000_000},
        {
            "type": "input_duration_limit",
            "observed_monotonic_ns": 9_100_000_000,
            "seconds": 8,
        },
        {
            "type": "resource_sample",
            "observed_monotonic_ns": 2_000_000_000,
            "input_queue_chunks": 0,
            "pending_vad_blocks": 0,
            "evidence_free_bytes": 1024**3,
            "temporary_free_bytes": 1024**3,
            "open_audio_files": 2,
            "process_peak_rss_bytes": 3200_000_000,
        },
    ]
    checked = {
        "status": "passed",
        "model_input_samples": 130048,
        "model": {"validation": {"terminal": "session_end", "utterances_final": 0}},
        "native": {
            "samples": {"microphone": 388800, "render": 388800},
            "bound_audio_chunks": 162,
            "defaults_unchanged": True,
            "private_aggregate_removed": True,
        },
        "native_to_model_samples_exact": True,
        "capture_ready_bound_to_audio": True,
        "fully_drained_replies": 0,
        "automatic_playback_interruptions": 0,
        "control_vad_p99_ms": 0.2,
    }

    def run():
        (tmp_path / "report.json").write_text(json.dumps(report))
        (tmp_path / "bridge-events.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in journal)
        )
        return summarize(tmp_path, checked, 8, True)

    return report, journal, checked, run


def test_quiet_soak_never_becomes_human_or_acoustic_qualification(fixture):
    result = fixture[3]()
    assert result["status"] == "passed"
    assert result["final_stt_turns"] == 0
    assert not result["human_conversation_qualified"]
    assert not result["acoustic_latency_qualified"]


@pytest.mark.parametrize(
    "case, reason",
    [
        ("missing_limit", "one readiness"),
        ("early", "early or stalled"),
        ("watchdog", "watchdog"),
        ("short_audio", "admitted audio"),
        ("queue", "queue exceeds"),
        ("no_resources", "insufficient resource"),
        ("low_disk", "disk below"),
    ],
)
def test_soak_refuses_false_continuity_or_resource_claims(fixture, case, reason):
    report, journal, checked, run = fixture
    if case == "missing_limit":
        journal.pop(1)
    elif case == "early":
        journal[1]["observed_monotonic_ns"] = 8_000_000_000
    elif case == "watchdog":
        report["native_terminal"]["reason"] = "duration_limit"
    elif case == "short_audio":
        checked["model_input_samples"] = 16_000
    elif case == "queue":
        journal[2]["pending_vad_blocks"] = 65
    elif case == "no_resources":
        journal.pop(2)
    elif case == "low_disk":
        journal[2]["evidence_free_bytes"] = 1
    with pytest.raises(ValueError, match=reason):
        run()
