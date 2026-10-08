"""Settings an engine cannot serve fail that session, not the engine.

Valid settings can still name something this engine does not hold: a speaking
language whose model is not installed, a preset absent from the selected
language. The core refuses the open with the reason. The worker used to treat
that refusal as its own failure and exit, so one unserved choice took speech
away until the engine restarted, and a stored setting would take it away again
at every session.

Production worker, deterministic models. The fixture's synthesizer holds only
the default speech settings and refuses every other, which is the refusal a
real engine gives for a language it has no model for.
"""
import os
from pathlib import Path

import pytest

from scripts.prove_native_worker_transport import Worker


@pytest.mark.parametrize("values", [{"tts_language": "fr"}, {"tts_voice": "marius"}, {"tts_language": "nl", "tts_voice": "vera"}])
def test_an_unserved_choice_fails_its_session_and_the_next_session_opens(tmp_path, values):
    w = Worker(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]), tmp_path / "unserved")
    try:
        q = w.open("unserved", settings=False)
        w.send({"settings_reply": {**q, "values": values}})
        failure = w.event("failure", "unserved")
        assert failure["reason"] == "speech settings unsupported by this backend", failure
        assert failure["resources_released"] is True, failure
        assert not [e for e in w.events if e["session_id"] == "unserved" and e["type"] == "session_ready"]

        w.open("served")  # replies with defaults and waits for session_ready
        assert w.status("served")["operator_settings"]["tts_language"] == "en"
        w.call("close", session_id="served", mode="abort")
        assert w.event("session_end", "served")["status"] == "aborted"
    finally:
        assert w.close() == 0  # the engine never failed: a clean exit


def test_a_language_outside_the_compiled_list_is_refused_when_the_settings_are_read(tmp_path):
    w = Worker(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]), tmp_path / "unknown")
    try:
        for name, values in (("speaking", {"tts_language": "xx"}), ("hearing", {"stt_language": "fr"})):
            q = w.open(name, settings=False)
            w.send({"settings_reply": {**q, "values": values}})
            reason = w.event("failure", name)["reason"]
            assert reason == ("unsupported speaking language" if name == "speaking" else "this recognition model supports English only")
    finally:
        assert w.close() == 0
