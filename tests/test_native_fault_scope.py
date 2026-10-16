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
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path

import pytest

from scripts.prove_native_worker_transport import Worker
from tests.native_limits import limits

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


def worker(tmp_path, name, table=None):
    """A worker under the table a test states, or the one the stand-in states where the test states none."""
    return Worker(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]), tmp_path / name, limits=table)


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
    # The wait is the opening's time of the table the carrier hands the worker, and no longer two seconds
    # compiled in: with 1.5 s stated, the session is refused after 1.5 s and long before the default.
    w = worker(tmp_path, "unanswered", limits(opening_ms=1500))
    try:
        began = time.monotonic()
        w.open("silent", settings=False)  # the carrier never answers the settings request
        failure = w.event("failure", "silent", timeout=6)
        waited = time.monotonic() - began
        assert failure["reason"] == "host settings: no answer from the carrier in time", failure
        assert 1.4 <= waited < 5, waited
        assert w.p.poll() is None
    finally:
        assert w.close() == 0, "a host's unanswered settings request failed the engine's exit"


def test_a_settings_read_that_failed_says_which_of_three_it_was(tmp_path):
    w = worker(tmp_path, "reasons")
    try:
        for sid, reason, said in (
            ("slow", "HOST_SETTINGS_NO_ANSWER", "host settings: no answer from the host in time"),
            ("refused", "HOST_SETTINGS_ERROR", "host settings: the host refused the read"),
            ("wrong", "HOST_SETTINGS_NOT_SETTINGS", "host settings: the host's answer is not settings"),
            ("older", None, "host settings unavailable"),  # a carrier that gives no reason
            ("other", "SOMETHING_NEW", "host settings unavailable"),
        ):
            query = w.open(sid, settings=False)
            reply = {**query, "error": "host settings unavailable or invalid"}
            if reason:
                reply["reason_code"] = reason  # the carrier's member (plugin/native/settings.go)
            w.send({"settings_reply": reply})
            failure = w.event("failure", sid)
            assert failure["reason"] == said, failure
            assert w.p.poll() is None, "the engine process ended with its session"
        w.open("after")  # and the engine opens the next session
        w.call("close", session_id="after", mode="abort")
        w.event("session_end", "after")
    finally:
        assert w.close() == 0


def test_a_limits_table_that_does_not_hold_stops_the_worker_before_it_is_ready(tmp_path):
    binary = Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"])
    for name, table in (
        ("not-nesting", limits(exchange_write_ms=250)),  # a write given less than a read
        ("out-of-range", limits(opening_ms=1)),
        ("a-member-missing", '{"exchange_read_ms":500}'),
        ("not-the-table", "fast"),
    ):
        # Started as its carrier starts it, and asked nothing: it ends by itself, says why, and never says ready.
        done = subprocess.run([str(binary), *["fixture"] * 7], env={**os.environ, "AII_VOICE_LIMITS": table},
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
        assert done.returncode != 0, name
        assert b"worker limits" in done.stderr, (name, done.stderr[-200:])
        assert b'"ready"' not in done.stdout, (name, done.stdout[-200:])
    # and a table that holds is taken: the same worker opens a session
    w = worker(tmp_path, "holds", limits())
    try:
        w.open("ok")
        w.call("close", session_id="ok", mode="abort")
        w.event("session_end", "ok")
    finally:
        assert w.close() == 0


def test_the_stand_in_states_a_table_as_a_carrier_does_and_never_one_it_inherited(tmp_path, monkeypatch):
    # A carrier always hands its worker a table, and always its own. The stand-in handed on whatever its own
    # environment held, or nothing. This process now inherits a table that cannot hold, which would stop a
    # worker before it is ready (the test above): the worker never sees it, whatever the test asks for.
    monkeypatch.setenv("AII_VOICE_LIMITS", limits(opening_ms=1))
    for name, asked, handed in (
        ("default", None, limits()),  # the table the carrier computes from its defaults
        ("stated", limits(opening_ms=1500), limits(opening_ms=1500)),
        ("none", Worker.NO_TABLE, None),  # no table at all: the worker's own compiled defaults
    ):
        w = worker(tmp_path, name, asked)
        try:
            assert w.limits == handed, name
            environment = Path(f"/proc/{w.p.pid}/environ")
            if environment.exists():  # Linux: what the worker's process was in fact started with
                entries = [e for e in environment.read_bytes().split(b"\0") if e.startswith(b"AII_VOICE_LIMITS=")]
                assert entries == ([] if handed is None else [b"AII_VOICE_LIMITS=" + handed.encode()]), name
            w.open("ok")
            w.call("close", session_id="ok", mode="abort")
            w.event("session_end", "ok")
        finally:
            assert w.close() == 0, name


def settings_lines(w):
    log = (w.out / "stderr.log").read_text()
    return [json.loads(line.removeprefix("AII_VOICE_SETTINGS ")) for line in log.splitlines() if line.startswith("AII_VOICE_SETTINGS ")]


def test_each_session_says_in_the_log_which_settings_it_was_given(tmp_path):
    # A preset is read when a session opens and at no other time. The log says which one each session had, so
    # that a voice heard after a save can be told from a session that predates it.
    w = worker(tmp_path, "settings-line")
    try:
        for sid, pause in (("first", 768), ("second", 1200)):
            q = w.open(sid, settings=False)
            w.configure(q, pause)
            w.event("session_ready", sid)
            w.call("close", session_id=sid, mode="abort")
            w.event("session_end", sid)
    finally:
        assert w.close() == 0
    lines = [l for l in settings_lines(w) if l["event"] == "session_settings"]
    assert [(l["component"], l["event"], l["session_id"]) for l in lines] == [("voice-worker", "session_settings", "first"), ("voice-worker", "session_settings", "second")], lines
    assert [l["settings"]["turn_pause_ms"] for l in lines] == [768, 1200], lines
    assert all(l["settings"]["tts_voice"] == "alba" and l["settings"]["tts_language"] == "en" and l["settings"]["stt_language"] == "en" for l in lines), lines
    # names and numbers the operator chose, and nothing else
    assert all(set(l["settings"]) == {"turn_pause_ms", "capture_limit_minutes", "vad_threshold", "tts_voice", "tts_language", "stt_language", "tts_temperature", "tts_seed"} for l in lines), lines


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


def test_an_opening_that_waits_for_its_settings_says_so_once_and_its_status_says_so_while_it_is_true(tmp_path):
    # The wait for a session's settings is the storage's whole wait now, not two seconds. A session that has been
    # opening for the table's opening_notice says what it is waiting for: slow is told from stuck.
    w = worker(tmp_path, "waits", limits(opening_ms=4000, opening_notice_ms=400))
    try:
        began = time.monotonic()
        query = w.open("slow", settings=False)
        opening = w.status("slow")["opening"]
        assert opening["waiting_for"] == "settings" and opening["waited_ms"] >= 0, opening
        assert not [l for l in settings_lines(w) if l["event"] == "opening_waits"], "said before the notice's time"
        while not [l for l in settings_lines(w) if l["event"] == "opening_waits"]:
            assert time.monotonic() - began < 3, "an opening that waited was never said to be waiting"
            time.sleep(.01)
        said = [l for l in settings_lines(w) if l["event"] == "opening_waits"]
        assert [(l["session_id"], l["waiting_for"]) for l in said] == [("slow", "settings")] and said[0]["waited_ms"] >= 400, said
        assert time.monotonic() - began >= .38
        time.sleep(.3)  # still waiting: it is said once, and the status goes on saying it
        assert len([l for l in settings_lines(w) if l["event"] == "opening_waits"]) == 1
        assert w.status("slow")["opening"]["waited_ms"] >= 600
        w.configure(query, 768)
        w.event("session_ready", "slow")
        assert "opening" not in w.status("slow"), "an open session still says it is opening"
        w.call("close", session_id="slow", mode="abort")
        w.event("session_end", "slow")
        # A session that opens inside the notice's time says nothing of the kind.
        w.open("quick")
        w.call("close", session_id="quick", mode="abort")
        w.event("session_end", "quick")
        assert len([l for l in settings_lines(w) if l["event"] == "opening_waits"]) == 1
    finally:
        assert w.close() == 0


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
    reply, _ = w.call("synthesize", session_id=sid, synthesis_id="reply", text="Break.")
    w.event("synthesis_start", sid)
    # synthesis_start says the reply was admitted, not that its synthesizer is inside it: a reply cancelled before
    # that ends cleanly and no engine fails. The reply's first audio says the synthesizer is there.
    deadline = time.monotonic() + 5
    while not any(f["kind"] == PCM and f["stream"] == reply["output_stream"] for f in w.frames):
        assert time.monotonic() < deadline, "the reply never spoke"
        time.sleep(.002)


def test_an_engine_failure_after_a_contained_session_failure_fails_the_exit(tmp_path):
    # The session's own fault is contained. The engine failure that follows it
    # in the same session is not that session's: it is said, and the exit
    # reports it.
    w = worker(tmp_path, "engine-after-contained")
    code = None
    try:
        engine_failure_pending(w, "s")
        # The session's fault is a gap too long because it needs no speech before it: speech beside a reply is a barge-in, which cancels the synthesizer by itself.
        w.input.write(frame(GAP, 1, 1, 30 * 16000 + 1))
        failure = w.event("failure", "s")
        assert "audio gap too long" in failure["reason"], failure
        reasons = [f["reason"] for f in failures(w)]
        assert len(reasons) == 2 and "audio gap too long" in reasons[0] and reasons[1] == "fixture synthesizer reset failed", reasons
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


def quiet(w, stream, samples, seq, start):
    while samples:
        n = min(1024, samples)
        seq += 1
        w.input.write(frame(PCM, stream, seq, start, b"\x00\x00" * n))
        start += n
        samples -= n
    return seq, start


def endpoint_lines(w):
    log = (w.out / "stderr.log").read_text()
    return [json.loads(line.removeprefix("AII_VOICE_ENDPOINT ")) for line in log.splitlines() if line.startswith("AII_VOICE_ENDPOINT ")]


def test_a_turn_waits_for_the_endpoints_verdict_the_tables_time(tmp_path, monkeypatch):
    # Where a pause would end a turn the engine asks its endpoint model whether the speaker has finished,
    # and waits for the verdict the table's endpoint_decision_ms, counted from when it asked. It was a
    # second typed in the endpoint's gate. Stated here as 300 ms, with an endpoint whose verdict takes 700:
    # the verdict is late, the worker's log says so with the member and its number, the turn ends by
    # silence alone, and nothing has failed. Under the second that was typed the verdict was in time and
    # nothing was said.
    monkeypatch.setenv("AII_FIXTURE_VAD", "level")
    monkeypatch.setenv("AII_FIXTURE_ENDPOINT_MS", "700")
    w = worker(tmp_path, "verdict", limits(endpoint_decision_ms=300))
    try:
        w.open("s")
        seq, pos = speech(w, 1, 4096)
        seq, pos = quiet(w, 1, 32768, seq, pos)  # past the longest silence a pause of 768 ms may be held for
        w.event("transcript_final", "s", timeout=20)
        assert endpoint_lines(w) == [dict(component="voice-worker", event="endpoint_decision_late", session_id="s",
                                          limit="endpoint_decision_ms", limit_ms=300)], endpoint_lines(w)
        finish(w, "s", 1, seq, pos)
        w.event("input_finished", "s", timeout=20)
        w.call("close", session_id="s", mode="drain")
        assert w.event("session_end", "s", timeout=20)["status"] == "completed"
        assert not failures(w), failures(w)
    finally:
        assert w.close() == 0


def test_an_endpoint_question_unanswered_at_the_inputs_end_is_waited_for_the_tables_time(tmp_path, monkeypatch):
    # A question the endpoint has not answered when a conversation's input ends is waited for the table's
    # endpoint_retire_ms. It was fifteen seconds typed in the endpoint's gate. A carrier holds this wait
    # over a model call's time, so the call's own limit speaks first; the table here is stated the other
    # way round, as no carrier states one, to see this wait by itself: 400 ms, with an endpoint that takes
    # five seconds. The session fails 400 ms after its input ended, and the failure says the time and the
    # member.
    stated = .4
    monkeypatch.setenv("AII_FIXTURE_VAD", "level")
    monkeypatch.setenv("AII_FIXTURE_ENDPOINT_MS", "5000")
    w = worker(tmp_path, "unanswered", limits(endpoint_decision_ms=300, endpoint_retire_ms=int(stated * 1000)))
    code = None
    try:
        w.open("s")
        seq, pos = speech(w, 1, 4096)
        seq, pos = quiet(w, 1, 12800, seq, pos)  # to where a pause of 768 ms would end the turn
        due = time.monotonic() + 20
        while not endpoint_lines(w):  # the verdict is late and its question is still owned
            assert time.monotonic() < due and w.p.poll() is None and not failures(w), failures(w)
            time.sleep(.01)
        began = time.monotonic()
        finish(w, "s", 1, seq, pos)
        failure = w.event("failure", "s", timeout=20)
        after = time.monotonic() - began
        assert failure["reason"] == ("semantic endpoint did not retire: 400 ms, the time the limits table gives "
                                     "it (endpoint_retire_ms)"), failure
        assert stated - .1 <= after < 4.5, (
            f"the session failed {after:.2f} s after its input ended; the table gives an unanswered question {stated} s")
    finally:
        code = w.close()
    assert code == 1


def test_a_captures_last_frames_are_waited_for_the_tables_time(tmp_path):
    # An enrollment capture that has been told the sample it ends at waits for the frames up to it the
    # table's capture_tail_ms, counted from when it was told. It was two seconds typed in the worker. Stated
    # here as three: a capture told where it ends and sent nothing fails three seconds on and no sooner,
    # and the failure says the time and the member. The worker is started with a speaker policy that takes
    # one recording, as a capture needs; the fault is the capture's own, so the exit does not report it.
    stated = 3
    policy = tmp_path / "policy.json"
    policy.write_text('{"calibration_sha256":"' + "c" * 64 + '","embedding_binding":"' + "b" * 64
                      + '","minimum_enrollment_samples":1,"minimum_margin":0.105,"threshold":0.56}')

    class Capturing(Worker):
        more = ("cpu", "fixture", str(policy))  # a backend, a speaker model and the policy's file

    w = Capturing(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]), tmp_path / "capture", uid=True,
                  limits=limits(capture_tail_ms=int(stated * 1000)))
    code = None
    try:
        opened, _ = w.call("open", session_id="c", input_handle="capture", output_handle="playback",
                           audio={"format": "s16le", "input": {"rate": 48000, "channels": 1},
                                  "output": {"rate": 48000, "channels": 2}},
                           enrollment_capture={"consented": True, "request_id": "ab" * 32, "created_ms": 1})
        assert opened["purpose"] == "enrollment_capture", opened
        w.event("session_ready", "c")
        began = time.monotonic()
        w.call("finish_input", session_id="c", stream_id="capture", end_sample=32000)
        failure = w.event("failure", "c", timeout=20)
        after = time.monotonic() - began
        assert failure["reason"] == ("enrollment capture final tail timeout: its last frames did not arrive in 3000 ms "
                                     "after it was told where it ends, the time the limits table gives them "
                                     "(capture_tail_ms)"), failure
        assert after >= stated - .1, (
            f"the capture failed {after:.2f} s after it was told where it ends; the table gives its last frames {stated} s")
    finally:
        code = w.close()
    assert code == 0


def test_a_conversations_last_frames_are_waited_for_the_tables_time(tmp_path):
    # The frames of a conversation up to the sample it was told it ends at are waited for the table's
    # input_tail_ms, by the session core. It was three seconds, a number the worker typed beside the
    # operator's settings whatever table it was handed. Stated here as 800 ms: a finish that names a sample
    # past what was sent, and nothing more sent, fails 800 ms on, and the failure says the time and the
    # member. It is a failure of the core, so the exit reports it.
    stated = .8
    w = worker(tmp_path, "tail", limits(input_tail_ms=int(stated * 1000)))
    code = None
    try:
        w.open("s")
        _, pos = speech(w, 1, 2048)
        began = time.monotonic()
        w.call("finish_input", session_id="s", stream_id="capture", end_sample=pos + 4096)
        failure = w.event("failure", "s", timeout=10)
        after = time.monotonic() - began
        assert failure["reason"] == ("input tail missing at admitted cutoff: 800 ms, the time the limits table "
                                     "gives it (input_tail_ms)"), failure
        assert stated - .1 <= after < 5, (
            f"the session failed {after:.2f} s after it was told where it ends; the table gives its last frames {stated} s")
    finally:
        code = w.close()
    assert code == 1


def test_a_final_waits_for_its_speaker_the_tables_time(tmp_path):
    # A final waits for its speaker the table's speaker_match_ms; then the wait is declared over and the
    # final's speaker is uncertain. It was fifteen seconds typed in the attribution. Stated here as 700 ms,
    # with a speaker model that does not answer until it is released: the observation comes 700 ms after
    # the final with the reason the attribution contract gives it, which carries no number, and the
    # worker's log names the limit that passed and its number.
    stated = .7
    w = Worker(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]), tmp_path / "match", uid=True,
               limits=limits(speaker_match_ms=int(stated * 1000)))
    try:
        w.open("s")
        seq, pos = speech(w, 1, 32137)
        finish(w, "s", 1, seq, pos)
        final = w.event("transcript_final", "s")
        began = time.monotonic()
        assert final["attribution"]["decision"] == "pending", final
        observation = w.event("speaker_observation", "s", timeout=10)
        after = time.monotonic() - began
        assert (observation["decision"], observation["reason"], observation["refers_to"]) == (
            "uncertain", "speaker_match_timeout", final["sequence"]), observation
        # Counted from when the final was admitted, a moment before it was read here.
        assert stated - .3 <= after < 5, (
            f"the wait for the speaker was declared over {after:.2f} s after the final; the table gives it {stated} s")
        log = (w.out / "stderr.log").read_text()
        said = [json.loads(line.removeprefix("AII_VOICE_SPEAKER ")) for line in log.splitlines() if line.startswith("AII_VOICE_SPEAKER ")]
        assert said == [dict(component="voice-worker", event="speaker_match_late", session_id="s",
                             refers_to=final["sequence"], limit="speaker_match_ms", limit_ms=700)], said
        w.call("close", session_id="s", mode="abort")
        w.event("session_end", "s")
    finally:
        assert w.close() == 0


def order_made(tmp_path, name, place):
    """Whether the fixture's seam says in the log that it failed the reset at the place it was asked to
    (worker_test_models.cpp, AII_FIXTURE_RESET_FAILS_AT). A run in which it did not showed nothing about that
    order, and fails where it would otherwise pass."""
    return f"AII_FIXTURE_ORDER the reset failed at {place}" in (tmp_path / name / "stderr.log").read_text().splitlines()


def test_an_engine_failure_between_a_passs_two_reads_of_the_core_is_said_after_a_contained_failure(tmp_path, monkeypatch):
    # The worker reads the core's status twice in a pass and ends the session on the second reading. It looked for
    # the core's failure after the first only, so a core that failed and retired between the two was released with
    # the failure unsaid: the log had the session's line alone and the exit reported success. The seam makes that
    # order the only one.
    monkeypatch.setenv("AII_FIXTURE_RESET_FAILS_AT", "last_status")
    test_an_engine_failure_after_a_contained_session_failure_fails_the_exit(tmp_path)
    assert order_made(tmp_path, "engine-after-contained", "last_status")


def test_an_engine_failure_between_a_passs_two_reads_of_the_core_fails_its_session(tmp_path, monkeypatch):
    # The same order with no failure before it: the session ended as completed, with no failure event, nothing in
    # the log and an exit of 0.
    monkeypatch.setenv("AII_FIXTURE_RESET_FAILS_AT", "last_status")
    test_the_same_engine_failure_alone_fails_the_exit(tmp_path)
    assert order_made(tmp_path, "engine-alone", "last_status")


def test_input_refused_by_a_core_that_has_failed_is_the_engines_failure(tmp_path, monkeypatch):
    # A core that has failed takes no more input. A frame fed before the worker has looked at the core again is
    # refused by it, and the refusal was said as the session's own fault: the failure event read "input admission
    # closed" and the engine's reason was only the log's second line. The seam fails the reset with a frame in
    # hand; nothing is wrong with the frame.
    monkeypatch.setenv("AII_FIXTURE_RESET_FAILS_AT", "input")
    w = worker(tmp_path, "refused-by-a-failed-core")
    code = None
    try:
        engine_failure_pending(w, "s")
        w.call("cancel_synthesis", session_id="s")  # answered, so the synthesizer is on its way to its reset
        speech(w, 1, 1024)
        failure = w.event("failure", "s")
        assert failure["reason"] == "fixture synthesizer reset failed", failure
        assert [f["reason"] for f in failures(w)] == ["fixture synthesizer reset failed"]
        assert order_made(tmp_path, "refused-by-a-failed-core", "input")
    finally:
        code = w.close()
    assert code == 1


def test_the_engine_failure_seam_is_fixture_only():
    # As the other seams are held: the calls stand under the macro that only the fixture target defines
    # (tests/test_native_output_only.py holds that), and what they call is defined only in the fixture's models.
    root = Path(__file__).resolve().parents[1] / "runtime/native/session"
    source, models = (root / "worker.cpp").read_text(), (root / "worker_test_models.cpp").read_text()
    for name in ("before_last_status", "before_input_frame"):
        call = "#ifdef AII_AUDIO_ACK_TEST_HOOK\n    " + name + "(session_);\n#endif\n"
        assert source.count(name + "(") == 2 and source.count(call) == 1, name
        assert models.count("void " + name + "(aii_voice_session *session)") == 1, name
    assert "AII_FIXTURE_" not in source


@pytest.mark.skipif(os.name == "nt", reason="reads the log through a datagram socket, where each write is one record")
def test_each_line_of_the_log_is_one_write(tmp_path):
    # The worker's standard error is the carrier's, and what reads that one stream cuts it at each newline. A line
    # written in pieces can have another writer's bytes between them: the log then holds two lines, and neither
    # is the line that was written. Standard error is a datagram socket here, so each write arrives as one record,
    # and every record must be one whole line.
    ours, workers = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    records, ended = [], threading.Event()

    def take():
        ours.settimeout(.05)
        while True:
            try:
                records.append(ours.recv(65536))
            except socket.timeout:
                if ended.is_set():
                    return

    reader = threading.Thread(target=take, daemon=True)
    reader.start()
    w = Worker(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]), tmp_path / "one-write", stderr=workers)
    try:
        w.open("s", stream=5)  # its settings
        w.input.write(frame(PCM, 6, 1, 0, b"\x00\x20" * 512))  # a frame of another stream
        w.input.write(frame(GAP, 5, 1, 1024))  # a gap that is filled
        w.input.write(frame(GAP, 5, 2, 1024 + 30 * 16000 + 1))  # and one too long: the session's failure
        w.event("failure", "s")
    finally:
        code = w.close()
        ended.set()
        reader.join()
        ours.close()
    assert code == 0
    for record in records:
        word, _, rest = record.partition(b" ")
        assert record.endswith(b"\n") and record.count(b"\n") == 1 and word.startswith(b"AII_VOICE_") and json.loads(rest), record
    assert {record.split(b" ", 1)[0].decode() for record in records} == {
        "AII_VOICE_SETTINGS", "AII_VOICE_FOREIGN_INPUT", "AII_VOICE_GAP", "AII_VOICE_FAILURE"}


def test_the_worker_has_one_writer_of_its_log():
    # Every line the worker itself writes goes through the one writer (worker_io.cpp, log_line).
    source = (Path(__file__).resolve().parents[1] / "runtime/native/session/worker.cpp").read_text()
    assert not [other for other in ("std::cerr", "std::clog", "stderr", "fputs(", "fprintf(") if other in source]
    assert "log_line(" in source


def test_a_worker_closed_with_a_session_open_ends_as_it_does_behind_a_carrier(tmp_path):
    # The stand-in ends the control channel and keeps the audio input open until the worker has exited, which is
    # the order a worker sees behind a carrier: the open session is aborted, nothing is said to have failed, and
    # the exit is 0. This does not show that the other order fails: that one failed only when the audio's end was
    # read first.
    w = worker(tmp_path, "closed-while-open")
    w.open("s")
    assert w.close() == 0
    ends = [e for e in w.events if e["session_id"] == "s" and e["type"] in ("session_end", "failure")]
    assert [(e["type"], e.get("status")) for e in ends] == [("session_end", "aborted")], ends
    assert failures(w) == []
