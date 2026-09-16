"""The new composition's closure must fail on contradicted native evidence."""

from pathlib import Path

import pytest

from scripts import audit_native_pocket_sdk as gate

EVIDENCE = Path(__file__).resolve().parents[1] / "deliverables/native-pocket-resident-20260910-r1/sdk-evidence-r1"


def test_real_native_composition_is_bound_and_preserves_original_failed_auditor():
    result = gate.audit(EVIDENCE)
    assert result["passed"] and result["original_identity_auditor_failure_preserved"]
    assert result["flat_loader_pcm_unchanged"] and len(result["component_waveforms"]) == 6
    assert result["sdk"]["audio"]["cycles"][0]["word_count"] == 14
    assert result["sdk"]["audio"]["cycles"][1]["word_count"] == 14
    assert not result["promoted"] and not result["qualified"]


@pytest.mark.parametrize("damage", [
    "false-component-pass", "blocking-cancel", "late-pcm", "false-limit-eos",
    "unbound-library", "unrelated-file-change", "wrong-parent", "false-owner-pass",
    "hidden-original-failure", "baseline-changed", "undersized-runtime", "disk-reserve", "leaked-owner",
])
def test_native_sdk_auditor_rejects_contradictions(monkeypatch, damage):
    read = gate.load

    def changed(path):
        row = read(path)
        name = path.relative_to(EVIDENCE).as_posix() if path.is_relative_to(EVIDENCE) else ""
        if name == "resident-proof-flat-r3/run/result.json":
            if damage == "false-component-pass": row["passed"] = False
            elif damage == "blocking-cancel": row["interruption"]["pending_after_ack"] = False
            elif damage == "late-pcm": row["interruption"]["late_pcm_refused"] = False
            elif damage == "false-limit-eos": row["frame_limit"]["state"] = 0
        if name == "sdk-runtime-r1/runtime/voice-runtime.json":
            if damage == "unbound-library": row["files"]["lib/native-pocket/native_pocket_resident.dll"]["sha256"] = "0" * 64
            elif damage == "wrong-parent": row["native_tts_derivation"]["baseline_manifest_sha256"] = "0" * 64
            elif damage == "unrelated-file-change": row["files"]["python/python.exe"]["sha256"] = "0" * 64
        if name == "sdk-owner-r1.json" and damage == "false-owner-pass": row["exit_code"] = 0
        if name == "sdk-runtime-r1/verdict.json":
            if damage == "hidden-original-failure": row["passed"] = True
            elif damage == "baseline-changed": row["baseline_preserved"] = False
            elif damage == "undersized-runtime": row["total_installed_bytes"] -= 1
            elif damage == "disk-reserve": row["free_bytes"] = 2*1024**3 - 1
        if name == "postflight.json" and damage == "leaked-owner": row["owners"] = [{"ProcessId": 1}]
        return row

    monkeypatch.setattr(gate, "load", changed)
    with pytest.raises((ValueError, AssertionError)):
        gate.audit(EVIDENCE)
