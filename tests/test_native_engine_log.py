import json
import os
from pathlib import Path

from scripts.prove_native_worker_transport import Worker

KEYS = {"turn_pause_ms", "capture_limit_minutes", "vad_threshold", "tts_voice", "tts_language", "stt_language",
        "tts_temperature", "tts_seed", "anonymous_profiles", "anonymous_expiry_days"}


def worker(tmp_path, name, monkeypatch, codes_only=True):
    if codes_only:
        monkeypatch.setenv("AII_VOICE_LOG_CODES_ONLY", "1")
    else:
        monkeypatch.delenv("AII_VOICE_LOG_CODES_ONLY", raising=False)
    return Worker(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]), tmp_path / name)


def log(w):
    return (w.out / "stderr.log").read_text()


def lines(w, prefix):
    return [json.loads(l.removeprefix(prefix + " ")) for l in log(w).splitlines() if l.startswith(prefix + " ")]


def test_a_sessions_settings_are_named_in_the_log_never_given(tmp_path, monkeypatch):
    w = worker(tmp_path, "named", monkeypatch)
    try:
        q = w.open("served", settings=False)
        w.configure(q, 1234)
        w.event("session_ready", "served")
        assert w.status("served")["operator_settings"]["turn_pause_ms"] == 1234
        w.call("close", session_id="served", mode="abort")
        w.event("session_end", "served")
    finally:
        assert w.close() == 0
    session = [l for l in lines(w, "AII_VOICE_SETTINGS") if l["event"] == "session_settings"]
    assert len(session) == 1 and set(session[0]["settings_keys"]) == KEYS and "settings" not in session[0], session
    for value in ("1234", "alba", '"en"', "20261009"):
        assert value not in log(w), value


def test_a_failure_says_its_code_and_withholds_its_words(tmp_path, monkeypatch):
    w = worker(tmp_path, "failure", monkeypatch)
    try:
        q = w.open("unserved", settings=False)
        w.send({"settings_reply": {**q, "values": {"tts_voice": "marius"}}})
        failure = w.event("failure", "unserved")
        assert failure["reason"] == "speech settings unsupported by this backend", failure
    finally:
        assert w.close() == 0
    said = lines(w, "AII_VOICE_FAILURE")
    assert [(l["session_id"], l["reason_withheld"], "reason" in l) for l in said] == [("unserved", True, False)], said
    assert "marius" not in log(w) and "unsupported" not in log(w)


def test_an_unreadable_correction_list_is_said_without_its_reason(tmp_path, monkeypatch):
    w = worker(tmp_path, "corrections", monkeypatch)
    try:
        q = w.open("listed", settings=False)
        w.send({"settings_reply": {**q, "values": {"turn_pause_ms": 768},
                                    "corrections": {"schema": "other", "revision": 1, "rules": []}}})
        ready = w.event("session_ready", "listed")
        assert ready["models"]["corrections"]["unreadable"], ready
    finally:
        assert w.close() == 0
    said = lines(w, "AII_VOICE_CORRECTIONS")
    assert len(said) == 1 and said[0]["reason_withheld"] is True and "reason" not in said[0], said


def test_without_the_flag_a_single_tenant_worker_logs_as_before(tmp_path, monkeypatch):
    w = worker(tmp_path, "before", monkeypatch, codes_only=False)
    try:
        q = w.open("served", settings=False)
        w.configure(q, 1234)
        w.event("session_ready", "served")
        w.call("close", session_id="served", mode="abort")
        w.event("session_end", "served")
    finally:
        assert w.close() == 0
    session = [l for l in lines(w, "AII_VOICE_SETTINGS") if l["event"] == "session_settings"]
    assert len(session) == 1 and session[0]["settings"]["turn_pause_ms"] == 1234 and "settings_keys" not in session[0], session
