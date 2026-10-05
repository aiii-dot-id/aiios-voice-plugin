"""A drain asked before the tail has arrived ends the session whole.

A host may ask for a drain as soon as the input's cutoff is fixed. Two things
then went wrong in the worker, and one order shows both:

- It learns that the core has retired from a status it reads after its poll
  for the core's events. The tail's final, input_finished and the retirement
  can all fall between the two, and the session ended cleanly without them.
- The audio lane's END frame names the cutoff the control already fixed. Read
  after the drain had released the core, it was refused as a late finish and
  the session failed where session_end was due (the core's own contract is in
  session_test.cpp).

Production worker, deterministic models. The fixture's seam holds each
draining pass between the poll and the status read, which is where the
scheduler has to put the core's last events for the first fault to show.
"""
import os
import struct
from pathlib import Path

from scripts.prove_native_worker_transport import Worker

PCM, END = 1, 3
TAIL = ("transcript_final", "turn_committed", "input_finished")


def frame(kind, stream, seq, start, payload=b""):
    return struct.pack(">4sB3xIIQI", b"AUD1", kind, stream, seq, start, len(payload)) + payload


def speech(w, stream, samples, seq=0, start=0):
    while samples:
        n = min(1024, samples)
        seq += 1
        w.input.write(frame(PCM, stream, seq, start, b"\x00\x20" * n))
        start += n
        samples -= n
    return seq, start


def held_worker(tmp_path, monkeypatch, name):
    monkeypatch.setenv("AII_FIXTURE_HOLD_AFTER_EVENT_POLL_MS", "30")
    return Worker(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]), tmp_path / name)


def ended_whole(w, sid):
    """session_end is the session's last event, after the whole tail, in a contiguous sequence."""
    w.event("session_end", sid, timeout=10)
    mine = [e for e in w.events if e["session_id"] == sid]
    kinds = [e["type"] for e in mine]
    assert "failure" not in kinds, [e for e in mine if e["type"] == "failure"]
    assert kinds[-1] == "session_end" and mine[-1]["status"] == "completed", kinds
    for kind in TAIL:
        assert kinds.count(kind) == 1, (kind, kinds)
    assert [e["sequence"] for e in mine] == list(range(1, len(mine) + 1)), kinds


def test_a_drain_asked_before_the_tail_ends_the_session_after_the_tails_events(tmp_path, monkeypatch):
    w = held_worker(tmp_path, monkeypatch, "tail")
    try:
        for stream in (1, 2, 3):
            sid = f"tail-{stream}"
            w.open(sid)
            seq, pos = speech(w, stream, 2048)
            w.call("finish_input", session_id=sid, stream_id="capture", end_sample=4096)
            w.call("close", session_id=sid, mode="drain")
            seq, pos = speech(w, stream, 2048, seq, pos)  # the tail, into a session already draining
            w.input.write(frame(END, stream, seq + 1, pos))
            ended_whole(w, sid)
            final = next(e for e in w.events if e["session_id"] == sid and e["type"] == "transcript_final")
            assert (final["start_sample"], final["end_sample"]) == (0, 4096), final
    finally:
        assert w.close() == 0


def test_the_event_poll_seam_is_fixture_only():
    root = Path(__file__).resolve().parents[1] / "runtime/native/session"
    worker = (root / "worker.cpp").read_text()
    call = "#ifdef AII_AUDIO_ACK_TEST_HOOK\n    after_event_poll(lifecycle_ == \"draining\");\n#endif\n"
    assert worker.count("after_event_poll(") == 2 and worker.count(call) == 1
    assert (root / "worker_test_models.cpp").read_text().count("void after_event_poll(bool draining)") == 1
