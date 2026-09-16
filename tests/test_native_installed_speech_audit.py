import json
from pathlib import Path

import pytest

from scripts.audit_native_installed_speech import audit

BASE = Path(__file__).resolve().parents[1]
EVIDENCE = BASE / "deliverables/native-streaming-20260909"
ROOT = EVIDENCE / "native-installed-speech-results-r3"


def review():
    return audit(ROOT, EVIDENCE / "native-installed-speech-source-r2.zip",
                 EVIDENCE / "native-installed-speech-results-r2.zip",
                 BASE / "scripts/resume_windows_native_speech.py")


def test_real_composed_windows_speech_passes_independent_audit():
    result = review()
    assert result["passed"] and not result["qualified"]
    assert result["speech"]["audio"]["complete_recovery_pcm_identical"]


@pytest.mark.parametrize("mutation", ["binding", "cpu_stt", "opening", "receipt", "retirement", "readback"])
def test_composed_voice_auditor_refuses_false_claims(monkeypatch, mutation):
    name = "speech/session-1/report.json"
    if mutation == "retirement":
        name = "owner.json"
    elif mutation == "readback":
        name = "verdict.json"
    path = ROOT / name
    body = json.loads(path.read_text())
    if mutation == "binding":
        body["packaged_runtime"]["manifest_sha256"] = "0" * 64
    elif mutation == "cpu_stt":
        ready = next(e for e in body["events"] if e["type"] == "session_ready")
        ready["models"]["models"]["stt"]["providers"]["encoder"] = ["CPUExecutionProvider"]
    elif mutation == "opening":
        body["transcript"] = " ".join(body["transcript"].split()[1:])
    elif mutation == "receipt":
        event = next(e for e in body["events"] if e["type"] == "playback_observation")
        event["rendered_samples"] += 1
    elif mutation == "retirement":
        body["previous_owner_and_child_retired"] = False
    else:
        body["readback"]["models"] = False
    original = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda p, *a, **kw: json.dumps(body) if p == path else original(p, *a, **kw))
    with pytest.raises(AssertionError):
        review()
