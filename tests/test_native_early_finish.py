"""A finish that arrives while its session is still opening keeps the words.

A page can end listening in the first moments of a session, before the engine
has its settings and has opened. The worker used to refuse that finish
("session not ready"); the audio admitted so far was held, never heard, and
lost when the session then closed, while the page said the words were kept.
The finish is now taken while opening and applied when the open completes.
Production worker, deterministic models.
"""
import os
import struct
from pathlib import Path

import pytest

from scripts.prove_native_worker_transport import Worker

PCM, END = 1, 3


def frame(kind, stream, seq, start, payload=b""):
    return struct.pack(">4sB3xIIQI", b"AUD1", kind, stream, seq, start, len(payload)) + payload


def speech(w, stream, samples):
    seq = start = 0
    while samples:
        n = min(1024, samples)
        seq += 1
        w.input.write(frame(PCM, stream, seq, start, b"\x00\x20" * n))
        start += n
        samples -= n
    return seq, start


def worker(tmp_path, name):
    return Worker(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]), tmp_path / name)


def refused(w, sid, end):
    w.counter += 1
    w.send({"id": w.counter, "operation": "speech.session.finish_input",
            "arguments": {"session_id": sid, "stream_id": "capture", "end_sample": end}})
    row = w.replies.get(timeout=2)
    assert row["id"] == w.counter and "error" in row, row
    return row["error"] if isinstance(row["error"], str) else row["error"]["message"]


def test_a_finish_taken_while_opening_is_applied_when_the_session_opens(tmp_path):
    w = worker(tmp_path, "early")
    try:
        query = w.open("early", False)  # the settings are not answered: the session is opening
        seq, end = speech(w, 1, 4096)
        result, _ = w.call("finish_input", session_id="early", stream_id="capture", end_sample=end)
        assert result["end_sample"] == end
        w.input.write(frame(END, 1, seq + 1, end))
        # The same end again is the same finish; another end is refused and changes nothing.
        w.call("finish_input", session_id="early", stream_id="capture", end_sample=end)
        assert "another end is already admitted" in refused(w, "early", end - 1024)
        w.configure(query, 768)
        w.event("session_ready", "early")
        final = w.event("transcript_final", "early")
        assert (final["start_sample"], final["end_sample"]) == (0, 4096)
        w.event("input_finished", "early")
        w.call("close", session_id="early", mode="drain")
        w.event("session_end", "early")
    finally:
        assert w.close() == 0


def test_an_early_finish_belongs_to_its_session_only(tmp_path):
    w = worker(tmp_path, "scoped")
    try:
        w.open("first", False)
        seq, end = speech(w, 1, 2048)
        w.call("finish_input", session_id="first", stream_id="capture", end_sample=end)
        w.call("close", session_id="first", mode="abort")  # aborted while still opening
        w.event("session_end", "first")
        # The next session has no end until it is given one: all of its speech is heard.
        w.open("second")
        seq, end = speech(w, 2, 6144)
        w.call("finish_input", session_id="second", stream_id="capture", end_sample=end)
        w.input.write(frame(END, 2, seq + 1, end))
        final = w.event("transcript_final", "second")
        assert (final["start_sample"], final["end_sample"]) == (0, 6144)
        w.call("close", session_id="second", mode="abort")
        w.event("session_end", "second")
    finally:
        assert w.close() == 0


@pytest.mark.parametrize("stream_id,reason", [("another", "foreign input handle")])
def test_an_early_finish_is_held_to_the_same_checks_as_a_late_one(tmp_path, stream_id, reason):
    w = worker(tmp_path, "checked")
    try:
        w.open("checked", False)
        w.counter += 1
        w.send({"id": w.counter, "operation": "speech.session.finish_input",
                "arguments": {"session_id": "checked", "stream_id": stream_id, "end_sample": 1024}})
        row = w.replies.get(timeout=2)
        assert row["id"] == w.counter and reason in str(row["error"]), row
        w.call("close", session_id="checked", mode="abort")
        w.event("session_end", "checked")
    finally:
        assert w.close() == 0
