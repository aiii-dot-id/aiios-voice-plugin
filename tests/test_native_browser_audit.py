"""The native browser claim must be backed by the selected real model owner."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.audit_native_browser import validate_models, validate_missing_receipt


def identity():
    result = Path(__file__).resolve().parents[1] / (
        "deliverables/native-streaming-20260909/"
        "windows-native-voice-results-r2/joint-proof/result.json"
    )
    return json.loads(result.read_text())["speech_models"]


def test_actual_combined_candidate_model_binding():
    assert validate_models(identity())["stt"]["backend"] == "native-directml"


@pytest.mark.parametrize("fault", ["parent", "child", "provider", "checkpoint"])
def test_a_label_cannot_certify_the_wrong_recognizer(fault):
    changed = deepcopy(identity())
    stt = changed["models"]["stt"]
    if fault == "parent":
        changed["backend"] = "windows-pocket-cpu-stt-cuda"
    elif fault == "child":
        stt["backend"] = "other-model"
    elif fault == "provider":
        stt["providers"]["encoder"] = ["CPUExecutionProvider"]
    else:
        stt["input_manifest_sha256"] = "not-the-verified-checkpoint"
    with pytest.raises(AssertionError):
        validate_models(changed)


def missing_receipt():
    path = Path(__file__).resolve().parents[1] / (
        "deliverables/native-streaming-20260909/"
        "windows-native-browser-results-r2/missing-terminal-receipt/report.json"
    )
    return json.loads(path.read_text())


def test_real_missing_receipt_leaves_only_playback_unobserved():
    validate_missing_receipt(missing_receipt())


@pytest.mark.parametrize("fault", ["input", "recognition", "synthesis", "playback", "dropped", "receipt", "cause", "invented_end"])
def test_an_unrelated_failure_is_not_the_missing_receipt_proof(fault):
    report = missing_receipt()
    browser = report["browser"]
    snapshot = browser["host_readback"]["snapshot"]
    if fault == "input":
        snapshot["input_completion"]["processed_end_sample"] -= 1
    elif fault == "recognition":
        snapshot["recognition"]["finalization_pending"] = True
    elif fault == "synthesis":
        snapshot["synthesis"]["state"] = "running"
    elif fault == "playback":
        snapshot["playback"]["state"] = "idle"
    elif fault == "dropped":
        browser["host_readback"]["outputs"]["4"]["Refused"] = 1
    elif fault == "receipt":
        next(c for c in browser["controls"] if c.get("suppressed_by_fixture"))["message"]["voice"]["playback"]["rendered"] -= 1
    elif fault == "cause":
        next(e for e in browser["events"] if e["type"] == "failure")["reason"] = "MODEL_LOAD_FAILED"
    else:
        browser["events"].append({"type": "session_end"})
    with pytest.raises(AssertionError):
        validate_missing_receipt(report)
