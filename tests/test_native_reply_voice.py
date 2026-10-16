"""A saved voice is the next reply's, not the next session's.

A preset was read when a session opened and at no other time, and a page keeps
one session open for as long as voice is on: a voice the operator saved changed
nothing they could hear until voice was turned off and on. The worker now asks
its carrier for the settings in force as each reply is admitted; the reply's
first segment waits for the answer, bounded, and the voice, its variation and
its seed are taken there, never inside a reply.

Production worker, deterministic models. The fixture's synthesizer holds two
voices and says which one spoke by the length of its audio: 960 samples for
the default, 480 for the second ("javert"). It refuses every other, and one
more ("fantine") that it accepts when asked and refuses when applied: a preset
removed in between.
"""
import json
import os
import time
from pathlib import Path

import pytest

from scripts.prove_native_worker_transport import Worker
from tests.native_limits import limits

DEFAULT, SECOND = 960, 480
BASE = {"turn_pause_ms": 768}


@pytest.fixture
def worker(tmp_path):
    w = Worker(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]), tmp_path / "worker")
    w.replies_spoken = 0
    try:
        yield w
    finally:
        assert w.close() == 0, "a change of voice failed the engine's exit"


def opened(w, sid="s"):
    q = w.open(sid, settings=False)
    w.send({"settings_reply": {**q, "values": dict(BASE)}})
    w.event("session_ready", sid)
    return sid


def reply(w, sid, text="One."):
    """One reply spoken to its end and its receipt given; the samples the engine spoke for it."""
    w.replies_spoken += 1
    name = f"reply-{w.replies_spoken}"
    result, _ = w.call("synthesize", session_id=sid, synthesis_id=name, text=text)
    stream = result["output_stream"]
    deadline = time.monotonic() + 5
    while not any((e["type"], e["session_id"], e.get("synthesis_id")) == ("synthesis_end", sid, name) for e in w.events):
        assert time.monotonic() < deadline, "the reply did not end"
        time.sleep(.002)
    while not any(f["stream"] == stream and f["kind"] == 3 for f in w.frames):
        assert time.monotonic() < deadline, "the reply's audio did not end"
        time.sleep(.002)
    samples = sum(f["samples"] for f in w.frames if f["stream"] == stream and f["kind"] == 1)
    w.call("playback_report", session_id=sid, synthesis_id=name, output_stream=stream, rendered_samples=samples, terminal=True)
    return samples


def lines(w, event=None):
    log = (w.out / "stderr.log").read_text()
    rows = [json.loads(line.removeprefix("AII_VOICE_SETTINGS ")) for line in log.splitlines() if line.startswith("AII_VOICE_SETTINGS ")]
    return [r for r in rows if event is None or r["event"] == event]


def voice_in_force(w, sid):
    return w.status(sid)["operator_settings"]["tts_voice"]


def test_a_saved_voice_is_the_next_replys_and_every_later_one(worker):
    sid = opened(worker)
    assert reply(worker, sid) == DEFAULT and voice_in_force(worker, sid) == "alba"
    worker.values[sid] = {**BASE, "tts_voice": "javert"}  # the operator saves another voice; the session stays open
    assert reply(worker, sid) == SECOND, "the voice saved was not the next reply's"
    assert reply(worker, sid) == SECOND, "the voice saved did not stay for the reply after"
    assert voice_in_force(worker, sid) == "javert", "the session's readback still names the voice it opened with"
    changed = lines(worker, "speech_settings")
    assert [(l["session_id"], l["settings"]["tts_voice"]) for l in changed] == [(sid, "javert")], changed  # said once
    worker.values[sid] = dict(BASE)  # and back
    assert reply(worker, sid) == DEFAULT
    assert [l["settings"]["tts_voice"] for l in lines(worker, "speech_settings")] == ["javert", "alba"]
    assert not lines(worker, "speech_settings_not_taken")
    # What a session reads at its opening and nowhere else stays that session's: only the voice was taken.
    worker.values[sid] = {**BASE, "turn_pause_ms": 1600, "tts_voice": "javert"}
    assert reply(worker, sid) == SECOND
    assert worker.status(sid)["operator_settings"]["turn_pause_ms"] == 768


@pytest.mark.parametrize("values,reason", [
    ({"tts_voice": "marius"}, "unsupported by this backend"),          # a voice this engine does not hold
    ({"tts_language": "fr"}, "new session"),                           # a language is another model
    ({"tts_voice": "nobody-at-all"}, "unsupported native voice preset"),  # not a preset at all
    ({"tts_voice": "fantine"}, "unsupported by this backend"),         # taken when asked, refused when applied
])
def test_a_change_that_cannot_be_taken_leaves_the_voice_in_force_and_is_said_once(worker, values, reason):
    sid = opened(worker)
    assert reply(worker, sid) == DEFAULT
    worker.values[sid] = {**BASE, **values}
    assert reply(worker, sid) == DEFAULT, "a change that could not be taken cost the reply its voice"
    assert reply(worker, sid) == DEFAULT
    assert voice_in_force(worker, sid) == "alba"
    refused = lines(worker, "speech_settings_not_taken")
    assert len(refused) == 1 and reason in refused[0]["reason"] and refused[0]["settings"]["tts_voice"] == "alba", refused
    assert not [e for e in worker.events if e["type"] == "failure"], "a change that could not be taken failed the session"
    # A voice it does hold is taken afterwards as any other.
    worker.values[sid] = {**BASE, "tts_voice": "javert"}
    assert reply(worker, sid) == SECOND


def test_a_reply_does_not_wait_past_its_bound_for_settings_that_do_not_come(tmp_path):
    w = Worker(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]), tmp_path / "worker", limits=limits(reply_settings_ms=400))
    w.replies_spoken = 0
    try:
        sid = opened(w)
        began = time.monotonic()
        assert reply(w, sid) == DEFAULT  # answered at once by the harness, as a host that is there
        assert time.monotonic() - began < .35, "a reply waited though its settings were answered"
        w.answer_refresh = False  # the host stops answering
        began = time.monotonic()
        assert reply(w, sid) == DEFAULT, "a reply whose settings did not come was not spoken in the voice in force"
        waited = time.monotonic() - began
        assert .38 <= waited < 2, waited
        # An answer that cannot be taken is still an answer: the reply does not go on waiting for one.
        w.answer_refresh = True
        w.values[sid] = {**BASE, "tts_voice": "marius"}
        began = time.monotonic()
        assert reply(w, sid) == DEFAULT
        assert time.monotonic() - began < .35, "a reply went on waiting after an answer it could not take"
        w.values[sid] = dict(BASE)
        w.answer_refresh = False
        began = time.monotonic()
        assert reply(w, sid) == DEFAULT
        assert time.monotonic() - began >= .38
        # The answer comes late, after its reply: it is the next reply's.
        w.refreshes.get(timeout=2)  # the first unanswered question: a newer one has been asked since
        late = w.refreshes.get(timeout=2)
        w.send({"settings_reply": {"id": late["id"], "session_id": sid, "values": {**BASE, "tts_voice": "javert"}}})
        assert reply(w, sid) == SECOND, "settings that came late were not the next reply's"
        # A failed read is not a change: the voice in force stays, and it is said.
        asked = w.refreshes.get(timeout=2)
        w.send({"settings_reply": {"id": asked["id"], "session_id": sid, "error": "host settings unavailable or invalid", "reason_code": "HOST_SETTINGS_ERROR"}})
        assert reply(w, sid) == SECOND
        said = [l["reason"] for l in lines(w, "speech_settings_not_taken")]
        assert len(said) == 2 and "unsupported by this backend" in said[0] and said[1] == "host settings: the host refused the read", said
    finally:
        assert w.close() == 0


def test_a_reply_asks_for_the_settings_alone(worker):
    sid = opened(worker)
    worker.answer_refresh = False
    result, _ = worker.call("synthesize", session_id=sid, synthesis_id="one", text="One.")
    asked = worker.refreshes.get(timeout=2)
    assert asked["session_id"] == sid and asked["refresh"] is True and set(asked) == {"id", "session_id", "refresh"}, asked
    worker.send({"settings_reply": {"id": asked["id"], "session_id": sid, "values": dict(BASE)}})
    deadline = time.monotonic() + 5
    while not any(f["stream"] == result["output_stream"] and f["kind"] == 3 for f in worker.frames):
        assert time.monotonic() < deadline
        time.sleep(.002)
    worker.call("close", session_id=sid, mode="abort")
    worker.event("session_end", sid)
