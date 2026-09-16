"""The throughput report must not launder cutoff/cancellation into completion."""

import copy

import pytest

from scripts.run_windows_native_qwen import analyze


def records():
    rows = []
    for i in range(6):
        rows.append({
            "event": "trial", "index": i, "text": "A complete sentence.",
            "structural_pass": True, "finite": True, "overflow": False,
            "status": -5 if i == 4 else 0, "cancel_at": .11 if i == 4 else -1,
            "eos_log_observed": i != 4, "seconds": .5, "audio_seconds": 1,
            "chunks": [{"at": .1, "start_sample": 0, "samples": 12000},
                       {"at": .4, "start_sample": 12000, "samples": 12000}],
        })
    return rows + [{"event": "complete", "structural_pass": True}]


def test_native_metrics_keep_scope_and_use_audio_seconds():
    got = analyze(records())
    assert len(got) == 6
    assert got[1]["first_chunk_seconds"] == .1
    assert got[1]["rtf_wall_over_audio"] == .5
    assert got[1]["additional_initial_buffer_needed_seconds"] == 0
    assert got[4]["cancel_to_return_seconds"] == pytest.approx(.39)


@pytest.mark.parametrize("kind", ["missing", "duplicate", "no_eos", "wrong_cancel", "gap",
                                  "duration", "time_backwards", "nonfinite", "overflow", "terminal"])
def test_native_report_rejects_false_completion(kind):
    rows = copy.deepcopy(records())
    if kind == "missing":
        rows.pop(0)
    elif kind == "duplicate":
        rows[1]["index"] = 0
    elif kind == "no_eos":
        rows[0]["eos_log_observed"] = False
    elif kind == "wrong_cancel":
        rows[4]["status"] = 0
    elif kind == "gap":
        rows[0]["chunks"][1]["start_sample"] += 1
    elif kind == "duration":
        rows[0]["audio_seconds"] += 1
    elif kind == "time_backwards":
        rows[0]["chunks"][1]["at"] = .05
    elif kind == "nonfinite":
        rows[0]["finite"] = False
    elif kind == "overflow":
        rows[0]["overflow"] = True
    elif kind == "terminal":
        rows[-1]["structural_pass"] = False
    with pytest.raises(ValueError):
        analyze(rows)


def test_native_report_exposes_gap_not_hidden_by_average_rtf():
    rows = records()
    rows[0]["chunks"][1]["at"] = .9
    rows[0]["seconds"] = .95
    got = analyze(rows)[0]
    assert got["rtf_wall_over_audio"] < 1
    assert got["additional_initial_buffer_needed_seconds"] == pytest.approx(.3)
