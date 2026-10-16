"""Real worker/core, deterministic models. Playback reports are not acoustic proof."""
import os
import time
from pathlib import Path

import pytest
from scripts.prove_native_worker_transport import Worker
from tests.native_limits import limits


def prepared(tmp_path):
    w = Worker(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]).resolve(), tmp_path / "worker")
    w.open("drain")
    w.call("synthesize", session_id="drain", synthesis_id="paragraph", text="Flood.")
    w.event("synthesis_end", "drain")
    end = time.monotonic() + 5
    while not any(f["kind"] == 3 for f in w.frames):
        assert time.monotonic() < end
        time.sleep(.002)
    stream = next(f["stream"] for f in w.frames if f["kind"] == 3)
    samples = sum(f["samples"] for f in w.frames if f["kind"] == 1)
    assert samples > 20 * 24000
    w.call("finish_input", session_id="drain", stream_id="capture", end_sample=0)
    w.event("input_finished", "drain")
    w.call("close", session_id="drain", mode="drain")
    return w, stream, samples


def test_advancing_playback_drains_beyond_old_wall_deadline(tmp_path):
    w, stream, samples = prepared(tmp_path)
    try:
        start = time.monotonic()
        while True:
            played = min(samples, int((time.monotonic() - start) * 24000))
            w.call("playback_report", session_id="drain", synthesis_id="paragraph",
                   output_stream=stream, rendered_samples=played, terminal=played == samples)
            if played == samples:
                break
            assert not any(e["type"] == "failure" for e in w.events)
            time.sleep(.25)
        assert time.monotonic() - start > 20
        assert w.event("session_end", "drain")["status"] == "completed"
        assert not any(e["type"] == "failure" for e in w.events)
    finally:
        assert w.close() == 0


@pytest.mark.parametrize("traffic", ["duplicate", "foreign"])
def test_nonprogress_cannot_keep_drain_alive(tmp_path, traffic):
    w, stream, _ = prepared(tmp_path)
    try:
        start = time.monotonic()
        while not any(e["type"] == "failure" for e in w.events):
            assert time.monotonic() - start < 19
            if w.p.poll() is not None:
                break
            w.counter += 1
            w.send({"id": w.counter, "operation": "speech.session.playback_report", "arguments": {
                "session_id": "drain" if traffic == "duplicate" else "retired-session",
                "synthesis_id": "paragraph", "output_stream": stream,
                "rendered_samples": 0, "terminal": False}})
            row = w.replies.get(timeout=2)
            assert row["id"] == w.counter
            if traffic == "duplicate" and row.get("error") == "session not ready":
                break  # the drain failed before its event was read; the event must still follow
            assert ("error" in row) == (traffic == "foreign"), row
            time.sleep(.25)
        failure = w.event("failure", "drain")
        assert failure["reason"] == ("native drain made no progress for 15000 ms, the time the limits table "
                                     "gives it (drain_idle_ms)")
        assert 14 <= time.monotonic() - start < 19
        assert failure["resources_released"] and not failure["playback_verified"]
    finally:
        assert w.close() != 0


def output_only(tmp_path, **stated):
    w = Worker(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]).resolve(), tmp_path / "worker", limits=limits(**stated))
    w.call("open", session_id="drain", output_handle="playback", audio={
        "format": "s16le", "input": None, "output": {"rate": 24000, "channels": 1}})
    w.configure(w.settings.get(timeout=2), 768)
    w.event("session_ready", "drain")
    return w


def test_a_model_call_longer_than_the_idle_limit_does_not_fail_a_drain(tmp_path, monkeypatch):
    """A drain is called stalled when nothing has moved for the table's
    drain_idle_ms. A model call in flight is not a stall: it has the table's
    model_call_ms, and the session's watchdog holds it to that. Stated here as
    2 s for the drain and 20 s for a call: a reply whose one model call takes
    5 s inside a drain is spoken and its session ends completed. The drain
    failed it at its own limit, with "no progress"."""
    monkeypatch.setenv("AII_FIXTURE_SLOW_CALL_MS", "5000")
    w = output_only(tmp_path, drain_idle_ms=2000, input_tail_ms=1000, model_call_ms=20000)
    try:
        reply, _ = w.call("synthesize", session_id="drain", synthesis_id="reply", text="Slow.")
        w.event("synthesis_start", "drain")
        began = time.monotonic()
        w.call("close", session_id="drain", mode="drain")
        w.event("synthesis_end", "drain", timeout=30)
        after = time.monotonic() - began
        assert after >= 4.5, f"the reply ended {after:.2f} s into the drain; its model call takes 5 s"
        stream = reply["output_stream"]
        due = time.monotonic() + 5
        while not any(f["stream"] == stream and f["kind"] == 3 for f in w.frames):
            assert time.monotonic() < due
            time.sleep(.002)
        count = sum(f["samples"] for f in w.frames if f["stream"] == stream)
        assert count == 960
        w.call("playback_report", session_id="drain", synthesis_id="reply", output_stream=stream,
               rendered_samples=count, terminal=True)
        assert w.event("session_end", "drain")["status"] == "completed"
        assert not any(e["type"] == "failure" for e in w.events), w.events
    finally:
        assert w.close() == 0


def test_a_model_call_past_its_own_limit_inside_a_drain_is_said_as_that(tmp_path):
    """The limit that speaks over a model call is the call's own. Stated here
    as 1 s for the drain and 3 s for a call: a reply whose model call never
    returns fails its session 3 s on with the watchdog's sentence, its number
    and its member, and not a second after the drain began with the drain's."""
    w = output_only(tmp_path, drain_idle_ms=1000, input_tail_ms=250, model_call_ms=3000)
    try:
        w.call("synthesize", session_id="drain", synthesis_id="reply", text="Hold.")
        w.event("synthesis_start", "drain")
        began = time.monotonic()
        w.call("close", session_id="drain", mode="drain")
        failure = w.event("failure", "drain", timeout=20)
        after = time.monotonic() - began
        assert failure["reason"] == ("synthesis model call exceeded progress deadline: 3000 ms, the time the "
                                     "limits table gives it (model_call_ms)"), failure
        assert failure["resources_released"] is True
        assert after >= 2.7, f"the session failed {after:.2f} s into its drain; the table gives a model call 3 s"
    finally:
        assert w.close() != 0


def test_a_drain_with_nothing_in_flight_fails_at_the_tables_idle_limit(tmp_path):
    """And a drain that is waiting on nothing with a limit of its own is still
    called stalled at drain_idle_ms. Stated here as 1.5 s: a reply written to
    the host and never reported played fails its session 1.5 s into the drain,
    and the failure names the member and its number."""
    stated = 1.5
    w = output_only(tmp_path, drain_idle_ms=int(stated * 1000), input_tail_ms=250)
    try:
        reply, _ = w.call("synthesize", session_id="drain", synthesis_id="reply", text="Short.")
        w.event("synthesis_end", "drain")
        stream = reply["output_stream"]
        due = time.monotonic() + 5
        while not any(f["stream"] == stream and f["kind"] == 3 for f in w.frames):
            assert time.monotonic() < due
            time.sleep(.002)
        began = time.monotonic()
        w.call("close", session_id="drain", mode="drain")
        failure = w.event("failure", "drain", timeout=20)
        after = time.monotonic() - began
        assert failure["reason"] == ("native drain made no progress for 1500 ms, the time the limits table "
                                     "gives it (drain_idle_ms)"), failure
        assert stated - .1 <= after <= stated + 1, (
            f"the drain failed {after:.2f} s on; the table gives it {stated} s with nothing moving")
    finally:
        assert w.close() != 0


def test_a_drains_idle_limit_runs_from_when_a_model_call_ended_in_it(tmp_path, monkeypatch):
    """A drain's idle limit is counted from its last work, and a model call
    that ends inside a drain is such work when it ends. Stated here as 3 s,
    with a reply whose model call takes one second of the drain: the reply's
    audio ends on the wire as the call does, nothing moves after it, and the
    drain is called stalled 3 s after that, within a second. The call's end
    used to be seen only when the deadline it ended inside had passed, and to
    renew it from there: twice the limit after the last work."""
    idle = 3
    monkeypatch.setenv("AII_FIXTURE_SLOW_CALL_MS", "1000")
    w = output_only(tmp_path, drain_idle_ms=idle * 1000, input_tail_ms=1000, model_call_ms=20000)
    try:
        reply, _ = w.call("synthesize", session_id="drain", synthesis_id="reply", text="Slow.")
        w.event("synthesis_start", "drain")
        w.call("close", session_id="drain", mode="drain")
        stream, due = reply["output_stream"], time.monotonic() + 30
        while not any(f["stream"] == stream and f["kind"] == 3 for f in w.frames):
            assert time.monotonic() < due and not any(e["type"] == "failure" for e in w.events), w.events
            time.sleep(.002)
        ended = time.monotonic()
        failure = w.event("failure", "drain", timeout=30)
        after = time.monotonic() - ended
        assert failure["reason"] == ("native drain made no progress for 3000 ms, the time the limits table "
                                     "gives it (drain_idle_ms)"), failure
        assert idle - .3 <= after <= idle + 1, (
            f"the drain was called stalled {after:.2f} s after its last work; the table gives it {idle} s with nothing moving")
    finally:
        assert w.close() != 0
