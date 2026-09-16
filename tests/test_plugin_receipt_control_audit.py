import copy
import json
from pathlib import Path

import pytest

from scripts.audit_plugin_receipt_control import audit, check

ROOT = (
    Path(__file__).resolve().parents[1]
    / "deliverables/plugin-sdk-engine-20260908/playback-control-mac-r1/reuse"
)


def test_real_sdk_receipt_control_evidence():
    assert audit(ROOT)["passed"]


@pytest.mark.parametrize(
    "mutation",
    [
        "lost_receipt",
        "foreign_session",
        "short_tail",
        "fake_acoustic",
        "refused_report",
        "abort_as_drain",
        "no_final_wait",
        "wrong_stream",
        "duplicate_event",
    ],
)
def test_receipt_auditor_rejects_false_completion(mutation):
    r = copy.deepcopy(json.loads((ROOT / "session-1/report.json").read_text()))
    observations = [e for e in r["events"] if e["type"] == "playback_observation"]
    if mutation == "lost_receipt":
        r["calls"] = [c for c in r["calls"] if c["operation"] != "playback_report"]
    elif mutation == "foreign_session":
        r["receipts"][-1]["arguments"]["session_id"] = "replaced-session"
    elif mutation == "short_tail":
        observations[-1]["rendered_samples"] -= 1
        r["receipts"][-1]["observation"] = observations[-1]
        r["receipts"][-1]["arguments"]["rendered_samples"] -= 1
    elif mutation == "fake_acoustic":
        r["events"][-1]["playback_verified"] = True
    elif mutation == "refused_report":
        c = next(c for c in r["calls"] if c["operation"] == "playback_report")
        c["reply"]["result"]["accepted"] = False
    elif mutation == "abort_as_drain":
        r["events"][-1]["status"] = "aborted"
    elif mutation == "no_final_wait":
        r["waiting_for_final_receipt"]["lifecycle"] = "closed"
    elif mutation == "wrong_stream":
        r["receipts"][-1]["arguments"]["output_stream"] += 1
    else:
        r["events"].insert(-1, dict(observations[-1]))
    with pytest.raises(AssertionError):
        check(r)
