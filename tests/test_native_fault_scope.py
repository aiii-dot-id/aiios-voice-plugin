"""A fault in one session's input ends that session, never the engine process.

The worker used to promote every exception in its loop to a process exit: one
malformed input frame in one session cost an engine restart (about 100 s on an
Apple Silicon desktop) and counted toward the supervisor's five-in-ten-minutes
deactivation. A contract fault in the input path (Refused) now fails only its
session, as a refused settings reply already did; the process stays ready for
the next session. Core failures and transport faults still end the process.
A session opened after a failed or aborted one never adopts frames of the
stream that ended. Production worker, deterministic models, not a soak.
"""
import json
import os
import struct
import time
from pathlib import Path

from scripts.prove_native_worker_transport import Worker

PCM, GAP, END = 1, 2, 3


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


def finish(w, sid, stream, seq, end):
    w.call("finish_input", session_id=sid, stream_id="capture", end_sample=end)
    w.input.write(frame(END, stream, seq + 1, end))


def worker(tmp_path, name):
    return Worker(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]), tmp_path / name)


def failures(w):
    log = (w.out / "stderr.log").read_text()
    return [json.loads(line.removeprefix("AII_VOICE_FAILURE ")) for line in log.splitlines() if line.startswith("AII_VOICE_FAILURE ")]


def test_an_input_fault_fails_its_session_and_the_next_session_transcribes(tmp_path):
    w = worker(tmp_path, "contained")
    try:
        w.open("first")
        seq, pos = speech(w, 1, 2048)
        w.input.write(frame(GAP, 1, seq + 1, 1024))  # a gap that runs backwards: a contract fault
        failure = w.event("failure", "first")
        assert "audio gap runs backwards" in failure["reason"], failure
        assert w.p.poll() is None, "the engine process ended with its session"
        w.open("second")
        seq, pos = speech(w, 2, 4096)
        finish(w, "second", 2, seq, pos)
        final = w.event("transcript_final", "second")
        assert (final["start_sample"], final["end_sample"]) == (0, 4096)
        w.call("close", session_id="second", mode="abort")
        w.event("session_end", "second")
        assert [f["session_id"] for f in failures(w)] == ["first"]
    finally:
        assert w.close() == 0


def test_a_new_session_never_adopts_the_ended_streams_frames(tmp_path):
    w = worker(tmp_path, "stale")
    try:
        w.open("old")
        seq_old, pos_old = speech(w, 1, 2048)
        w.call("close", session_id="old", mode="abort")
        w.event("session_end", "old")
        w.open("new")
        # Frames of the ended stream still in the pipe arrive first, then the new stream's.
        speech(w, 1, 1024, seq=seq_old, start=pos_old)
        seq, pos = speech(w, 2, 3072)
        finish(w, "new", 2, seq, pos)
        final = w.event("transcript_final", "new")
        assert (final["start_sample"], final["end_sample"]) == (0, 3072), final
        assert not any(e["type"] == "failure" for e in w.events), w.events
        w.call("close", session_id="new", mode="abort")
        w.event("session_end", "new")
    finally:
        assert w.close() == 0


def test_a_lost_audio_endpoint_still_ends_the_process(tmp_path):
    w = worker(tmp_path, "transport")
    w.open("pipe")
    speech(w, 1, 1024)
    w.input.close()  # the audio endpoint is gone: a transport fault, not a session's
    failure = w.event("failure", "pipe")
    assert "audio endpoint lost" in failure["reason"], failure
    assert w.close() != 0


def held_lines(w):
    log = (w.out / "stderr.log").read_text()
    return [json.loads(line.removeprefix("AII_VOICE_BACKPRESSURE ")) for line in log.splitlines() if line.startswith("AII_VOICE_BACKPRESSURE ")]


def test_input_held_over_a_second_is_declared_with_its_end(tmp_path):
    w = worker(tmp_path, "held")
    try:
        # Control half: a session that consumes its input at once declares nothing.
        w.open("quick")
        seq, pos = speech(w, 1, 4096)
        finish(w, "quick", 1, seq, pos)
        w.event("transcript_final", "quick")
        w.call("close", session_id="quick", mode="abort")
        w.event("session_end", "quick")
        assert held_lines(w) == []
        # A session whose settings are not yet answered cannot consume: its input is held.
        query = w.open("slow", settings=False)
        seq, pos = speech(w, 2, 2048)
        time.sleep(1.4)
        w.configure(query, 768)
        w.event("session_ready", "slow")
        seq, pos = speech(w, 2, 2048, seq=seq, start=pos)
        finish(w, "slow", 2, seq, pos)
        w.event("transcript_final", "slow")
        lines = held_lines(w)
        assert [l["event"] for l in lines] == ["input_backpressure", "input_backpressure_cleared"], lines
        assert all(l["session_id"] == "slow" for l in lines)
        assert lines[0]["held_ms"] >= 1000 and lines[1]["held_ms"] >= lines[0]["held_ms"]
        w.call("close", session_id="slow", mode="abort")
        w.event("session_end", "slow")
    finally:
        assert w.close() == 0


def test_a_new_session_may_reuse_the_ended_streams_id(tmp_path):
    # Stream ids are the host's to choose; a host may number every session's
    # input stream alike (the SDK proof host does). A new session's first frame
    # starts at its own sample 0, which no leftover frame of a finished stream
    # does, so reuse is its own input, never mistaken for a stale stream.
    w = worker(tmp_path, "reuse")
    try:
        for sid in ("one", "two", "three"):
            w.open(sid)
            seq, pos = speech(w, 7, 3072)
            finish(w, sid, 7, seq, pos)
            final = w.event("transcript_final", sid)
            assert (final["start_sample"], final["end_sample"]) == (0, 3072), (sid, final)
            w.call("close", session_id=sid, mode="abort")
            w.event("session_end", sid)
        assert not any(e["type"] == "failure" for e in w.events), w.events
    finally:
        assert w.close() == 0
