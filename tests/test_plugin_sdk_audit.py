"""Retained real-run audit falsifiers; no models or live devices are invoked."""

import copy
import json
from pathlib import Path

import pytest

from scripts.audit_plugin_sdk_proof import audit

BASE = Path(__file__).resolve().parents[1] / "deliverables/plugin-sdk-engine-20260908"


@pytest.mark.parametrize(
    "name",
    [
        "windows-r2/speech-controls",
        "windows-r2/speech-interrupt",
        "mlx-port-regression-r4",
    ],
)
def test_bound_real_sdk_evidence(name):
    path = BASE / name
    if not path.exists():
        pytest.skip("the retained platform evidence is not present")
    assert audit(path)["passed"]


@pytest.mark.parametrize(
    "mutation",
    ["transcript", "source", "span", "tail", "post_terminal", "playback_claim"],
)
def test_real_evidence_mutations_are_refused(mutation):
    path = BASE / "windows-r2/speech-interrupt"
    if not path.exists():
        pytest.skip("the retained Windows evidence is not present")
    report = copy.deepcopy(json.loads((path / "report.json").read_text()))
    if mutation == "transcript":
        report["transcript"] = report["transcript"].replace("Please", "")
    elif mutation == "source":
        report["source_sha256"][next(iter(report["source_sha256"]))] = "0" * 64
    elif mutation == "span":
        report["frame_spans"][0]["start"] += 1
    elif mutation == "tail":
        next(e for e in report["events"] if e["type"] == "synthesis_end")[
            "delivered_samples"
        ] += 1
    elif mutation == "post_terminal":
        event = dict(
            report["events"][-1],
            type="transcript_partial",
            sequence=len(report["events"]) + 1,
        )
        report["events"].append(event)
    else:
        report["events"][-1]["playback_verified"] = True
    with pytest.raises(AssertionError):
        audit(path, report)
