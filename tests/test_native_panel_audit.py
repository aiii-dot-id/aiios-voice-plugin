from copy import deepcopy
from pathlib import Path

import pytest

from scripts.audit_native_streaming_panel import audit_case
from scripts.stt_metrics import transcript_metrics


@pytest.fixture
def evidence(monkeypatch):
    monkeypatch.setattr("scripts.prove_native_streaming_panel.pcm", lambda _: [0.1] * 8)
    sample = {
        "id": "a",
        "condition": "clean",
        "reference_text": "opening word tail",
        "audio_sha256": "a" * 64,
    }
    case = {
        "sample": sample,
        "panel": "fixed",
        "paced": False,
        "path": Path("unused.wav"),
    }
    row = {
        "id": "a",
        "condition": "clean",
        "reference": sample["reference_text"],
        "audio_sha256": sample["audio_sha256"],
        "panel": "fixed",
        "paced": False,
        "samples": 8,
        "terminal": {
            "event": "final",
            "text": sample["reference_text"],
            "captured_samples": 8,
            "input_finished": True,
            "decoder_ready_after_finish": False,
            "model_padding_samples": 10560,
        },
        "metrics": transcript_metrics(
            sample["reference_text"], sample["reference_text"]
        ),
    }
    return row, case


def test_clean_native_case(evidence):
    row, case = evidence
    assert audit_case(row, case)["word_errors"] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("captured_samples", 7),
        ("event", "error"),
        ("input_finished", False),
        ("decoder_ready_after_finish", True),
        ("model_padding_samples", 0),
        ("text", "opening word"),
    ],
)
def test_audit_refuses_missing_tail_false_completion_and_invented_score(
    evidence, field, value
):
    row, case = deepcopy(evidence)
    row["terminal"][field] = value
    with pytest.raises(ValueError):
        audit_case(row, case)


def test_empty_transcript_is_counted_not_dropped(evidence):
    row, case = evidence
    row["terminal"]["text"] = ""
    row["metrics"] = transcript_metrics(row["reference"], "")
    assert audit_case(row, case)["word_errors"] == 3
