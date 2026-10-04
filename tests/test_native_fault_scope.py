"""A fault in one session's input ends that session, never the engine process.

The worker used to promote every exception in its loop to a process exit: one
malformed input frame in one session cost an engine restart (about 100 s on an
Apple Silicon desktop) and counted toward the supervisor's five-in-ten-minutes
deactivation. A contract fault in the input path (Refused) now fails only its
session, as a refused settings reply already did; the process stays ready for
the next session. Core failures and transport faults still end the process,
and an engine failure that follows a contained session failure is still said
and still fails the exit.
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
        # The old stream must have begun in its own session; a session ended
        # before any frame was read cannot say which stream was its.
        deadline = time.monotonic() + 5
        while w.status("old")["input"]["received_end_sample"] < pos_old:
            assert time.monotonic() < deadline, "the old session never received its audio"
            time.sleep(.01)
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


def test_a_refused_settings_reply_fails_its_session_not_the_engines_exit(tmp_path):
    # The failure is the last thing before shutdown: a later open would clear it.
    w = worker(tmp_path, "settings")
    try:
        query = w.open("bad", settings=False)
        w.configure(query, 100)  # below the 320 ms floor: the session's contract refuses it
        failure = w.event("failure", "bad")
        assert "pause must be 320" in failure["reason"], failure
        assert w.p.poll() is None, "the engine process ended with its session"
    finally:
        assert w.close() == 0, "a session's refused settings failed the engine's exit"


def test_an_unanswered_settings_request_fails_its_session_not_the_engines_exit(tmp_path):
    w = worker(tmp_path, "unanswered")
    try:
        w.open("silent", settings=False)  # the host never answers the settings request
        failure = w.event("failure", "silent", timeout=6)
        assert failure["reason"] == "settings preparation timeout", failure
        assert w.p.poll() is None
    finally:
        assert w.close() == 0, "a host's unanswered settings request failed the engine's exit"


def foreign_lines(w):
    log = (w.out / "stderr.log").read_text()
    return [json.loads(line.removeprefix("AII_VOICE_FOREIGN_INPUT ")) for line in log.splitlines() if line.startswith("AII_VOICE_FOREIGN_INPUT ")]


def aborted_then_reopened(w, declare):
    """An aborted session's audio the engine never read arrives after the next open."""
    w.open("old", stream=11 if declare else None)
    w.call("close", session_id="old", mode="abort")
    w.event("session_end", "old")
    w.open("new", stream=12 if declare else None)
    speech(w, 11, 2048)  # the aborted session's unread frames: they start at sample 0
    return speech(w, 12, 3072)


def test_a_declared_stream_drops_an_aborted_sessions_unread_audio(tmp_path):
    w = worker(tmp_path, "declared")
    try:
        seq, pos = aborted_then_reopened(w, declare=True)
        finish(w, "new", 12, seq, pos)
        final = w.event("transcript_final", "new")
        assert (final["start_sample"], final["end_sample"]) == (0, 3072), final
        status = w.status("new")["input"]
        assert (status["stream"], status["foreign_frames"]) == (12, 2), status
        assert foreign_lines(w) == [{"component": "voice-worker", "event": "foreign_input",
                                     "session_id": "new", "stream": 11, "declared": 12}]
        assert not any(e["type"] == "failure" for e in w.events), w.events
        w.call("close", session_id="new", mode="abort")
        w.event("session_end", "new")
    finally:
        assert w.close() == 0


def test_without_a_declared_stream_the_unread_audio_is_adopted(tmp_path):
    # Control half: the same order with no declaration is the race the field closes.
    w = worker(tmp_path, "undeclared")
    try:
        aborted_then_reopened(w, declare=False)
        failure = w.event("failure", "new")
        assert "audio stream/sequence differs" in failure["reason"] or "clock differs" in failure["reason"], failure
        assert w.status("new")["input"]["stream"] is None
        assert foreign_lines(w) == []
    finally:
        assert w.close() == 0


def test_a_stream_number_serves_one_session(tmp_path):
    w = worker(tmp_path, "reuse")
    try:
        w.open("first", stream=7)
        w.call("close", session_id="first", mode="abort")
        w.event("session_end", "first")
        w.counter += 1
        w.send({"id": w.counter, "operation": "speech.session.open", "arguments": {
            "session_id": "second", "input_handle": "capture", "output_handle": "playback",
            "audio": {"format": "s16le", "input": {"rate": 48000, "channels": 1, "stream": 7},
                      "output": {"rate": 48000, "channels": 2}}}})
        row = w.replies.get(timeout=2)
        assert row["id"] == w.counter and row.get("error") == "input stream number already served an earlier session", row
        w.open("third", stream=8)  # a fresh number opens; the refusal left no session behind
        w.call("close", session_id="third", mode="abort")
        w.event("session_end", "third")
    finally:
        assert w.close() == 0


def open_speaker_only(w, sid):
    """A session with no input direction: audio.input is null and it carries no input handle."""
    result, _ = w.call("open", session_id=sid, output_handle="playback",
                       audio={"format": "s16le", "input": None, "output": {"rate": 48000, "channels": 2}})
    assert result["audio"] == {"input": None, "output": {"rate": 24000, "channels": 1}}
    w.configure(w.settings.get(timeout=2), 768)
    w.event("session_ready", sid)


def aborted_then_speaker_only(w, declare):
    """Capture is aborted and a speaker-only session opens; the aborted session's unread audio arrives after it."""
    w.open("old", stream=11 if declare else None)
    w.call("close", session_id="old", mode="abort")
    w.event("session_end", "old")
    open_speaker_only(w, "new")
    speech(w, 11, 2048)  # two frames the aborted session never read: they start at sample 0


def foreign_frames(w, sid, want):
    until = time.monotonic() + 5
    while time.monotonic() < until and w.status(sid)["input"]["foreign_frames"] < want:
        time.sleep(.01)
    return w.status(sid)["input"]["foreign_frames"]


def test_a_speaker_only_session_drops_an_aborted_sessions_declared_audio(tmp_path):
    # A speaker-only session has no input stream to declare. The aborted
    # session declared its own, and each number serves one session, so its
    # frames are that session's wherever they arrive.
    w = worker(tmp_path, "speaker-only")
    try:
        aborted_then_speaker_only(w, declare=True)
        assert foreign_frames(w, "new", 2) == 2
        status = w.status("new")
        assert (status["input"]["stream"], status["input"]["state"]) == (None, "absent"), status
        assert foreign_lines(w) == [{"component": "voice-worker", "event": "foreign_input",
                                     "session_id": "new", "stream": 11, "declared": None}]
        assert not any(e["type"] == "failure" for e in w.events), w.events
        assert failures(w) == []
        w.call("close", session_id="new", mode="abort")
        w.event("session_end", "new")
    finally:
        assert w.close() == 0


def test_a_speaker_only_session_still_refuses_audio_no_session_declared(tmp_path):
    # Control half: a stream no earlier session declared is not an earlier
    # session's audio. A host that writes it to a session with no input
    # direction is refused, as before.
    w = worker(tmp_path, "speaker-only-undeclared-stream")
    try:
        w.open("old", stream=11)
        w.call("close", session_id="old", mode="abort")
        w.event("session_end", "old")
        open_speaker_only(w, "new")
        speech(w, 99, 1024)
        failure = w.event("failure", "new")
        assert "no input direction" in failure["reason"], failure
        assert foreign_lines(w) == []
        assert w.p.poll() is None, "the engine process ended with its session"
    finally:
        assert w.close() == 0


def test_without_a_declared_stream_a_speaker_only_session_refuses_the_unread_audio(tmp_path):
    # Control half: a host that declares no stream leaves the engine nothing
    # to tell the aborted session's frames by. The older behaviour stands.
    w = worker(tmp_path, "speaker-only-no-declaration")
    try:
        aborted_then_speaker_only(w, declare=False)
        failure = w.event("failure", "new")
        assert "no input direction" in failure["reason"], failure
        assert foreign_lines(w) == []
    finally:
        assert w.close() == 0


def engine_failure_pending(w, sid):
    """A reply whose synthesizer cannot be restored once it is cancelled: an engine failure waiting for the cancel."""
    w.open(sid)
    w.call("synthesize", session_id=sid, synthesis_id="reply", text="Break.")
    w.event("synthesis_start", sid)


def test_an_engine_failure_after_a_contained_session_failure_fails_the_exit(tmp_path):
    # The session's own fault is contained. The engine failure that follows it
    # in the same session is not that session's: it is said, and the exit
    # reports it.
    w = worker(tmp_path, "engine-after-contained")
    code = None
    try:
        engine_failure_pending(w, "s")
        seq, _ = speech(w, 1, 2048)
        w.input.write(frame(GAP, 1, seq + 1, 1024))  # a gap that runs backwards: the session's contract fault
        failure = w.event("failure", "s")
        assert "audio gap runs backwards" in failure["reason"], failure
        reasons = [f["reason"] for f in failures(w)]
        assert len(reasons) == 2 and "audio gap runs backwards" in reasons[0] and reasons[1] == "fixture synthesizer reset failed", reasons
    finally:
        code = w.close()
    assert code == 1, "an engine failure after a contained session failure left the exit reporting success"


def test_the_same_engine_failure_alone_fails_the_exit(tmp_path):
    # Control half: without a session fault before it, the engine failure is
    # the first cause and fails the exit, before this change and after it.
    w = worker(tmp_path, "engine-alone")
    code = None
    try:
        engine_failure_pending(w, "s")
        w.call("cancel_synthesis", session_id="s")
        failure = w.event("failure", "s")
        assert failure["reason"] == "fixture synthesizer reset failed", failure
        assert [f["reason"] for f in failures(w)] == ["fixture synthesizer reset failed"]
    finally:
        code = w.close()
    assert code == 1
