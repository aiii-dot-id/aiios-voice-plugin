"""A gap the host declares in the input stream must not end the session, or the engine.

The host's audio plane declares a gap (AUD1 kind 2) when its queue overflowed
or the page's capture was lost. The worker used to treat any such frame as a
fault: the session failed, the carrier exited, and the host restarted the
engine, which takes about a minute and a half. Found live on a phone page, the
failure line was "input ended or discontinuous". The worker now fills the
declared gap with the silence it replaced, keeps its input clock continuous,
and says so on its own diagnostic line. Run on the production worker with
deterministic models, not a soak.
"""
import json
import os
import struct
import time
from pathlib import Path

from scripts.prove_native_worker_transport import Worker

PCM, GAP, END = 1, 2, 3


def frame(kind, seq, start, payload=b""):
    return struct.pack(">4sB3xIIQI", b"AUD1", kind, 1, seq, start, len(payload)) + payload


class Stream:
    """The page's side of one input stream: sequence and sample position."""

    def __init__(self, w):
        self.w, self.seq, self.pos = w, 0, 0

    def pcm(self, n):
        self.seq += 1
        self.w.input.write(frame(PCM, self.seq, self.pos, b"\x00\x20" * n))
        self.pos += n

    def speech(self, samples):
        while samples:
            n = min(1024, samples)
            self.pcm(n)
            samples -= n

    def gap(self, lost, seq=None):
        """The host declares `lost` samples missing; capture resumes after them."""
        self.seq = self.seq + 1 if seq is None else seq
        self.pos += lost
        self.w.input.write(frame(GAP, self.seq, self.pos))

    def end(self, sid):
        self.w.call("finish_input", session_id=sid, stream_id="capture", end_sample=self.pos)
        self.w.input.write(frame(END, self.seq + 1, self.pos))


def gaps_declared(w):
    log = (w.out / "stderr.log").read_text()
    return [json.loads(line.removeprefix("AII_VOICE_GAP ")) for line in log.splitlines() if line.startswith("AII_VOICE_GAP ")]


def failures(w):
    log = (w.out / "stderr.log").read_text()
    return [json.loads(line.removeprefix("AII_VOICE_FAILURE ")) for line in log.splitlines() if line.startswith("AII_VOICE_FAILURE ")]


def worker(tmp_path, name):
    return Worker(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]), tmp_path / name)


def test_a_declared_gap_is_filled_declared_and_the_session_continues(tmp_path):
    w = worker(tmp_path, "survives")
    try:
        w.open("gap")
        s = Stream(w)
        s.speech(10240)
        s.gap(4000)
        s.speech(10240)
        s.end("gap")
        final = w.event("transcript_final", "gap")
        # The clock never jumped: the final spans every sample, the gap included.
        assert (final["start_sample"], final["end_sample"]) == (0, 24480)
        assert gaps_declared(w) == [{"component": "voice-worker", "event": "input_gap", "session_id": "gap", "start": 10240, "samples": 4000}]
        assert not any(e["type"] == "failure" for e in w.events), w.events
        w.call("close", session_id="gap", mode="abort")
        w.event("session_end", "gap")
        assert failures(w) == []
    finally:
        assert w.close() == 0


def test_a_long_gap_is_filled_across_passes_and_every_gap_is_declared(tmp_path):
    w = worker(tmp_path, "long")
    try:
        w.open("long")
        s = Stream(w)
        s.speech(2048)
        s.gap(100000)  # far more than one pass feeds: the worker resumes it
        s.speech(2048)
        s.gap(512)
        s.speech(1024)
        s.end("long")
        final = w.event("transcript_final", "long")
        assert final["end_sample"] == s.pos == 2048 + 100000 + 2048 + 512 + 1024
        assert [(g["start"], g["samples"]) for g in gaps_declared(w)] == [(2048, 100000), (104096, 512)]
        w.call("close", session_id="long", mode="abort")
        w.event("session_end", "long")
    finally:
        assert w.close() == 0


def test_a_zero_length_discontinuity_is_accepted_and_declares_nothing(tmp_path):
    w = worker(tmp_path, "zero")
    try:
        w.open("zero")
        s = Stream(w)
        s.speech(2048)
        s.gap(0)
        s.speech(2048)
        s.end("zero")
        assert w.event("transcript_final", "zero")["end_sample"] == 4096
        assert gaps_declared(w) == []
        w.call("close", session_id="zero", mode="abort")
        w.event("session_end", "zero")
    finally:
        assert w.close() == 0


def refused(tmp_path, name, reason, act):
    """The input is a fault: the session fails with the reason; the engine process stays (fault scope)."""
    w = worker(tmp_path, name)
    try:
        w.open(name)
        s = Stream(w)
        s.speech(2048)
        act(s, w, name)
        failure = w.event("failure", name)
        assert reason in failure["reason"], failure
        assert [f["reason"] for f in failures(w)] == [failure["reason"]]
        assert w.p.poll() is None, "the engine process ended with its session"
    finally:
        assert w.close() == 0


def test_a_gap_that_runs_backwards_is_still_a_fault(tmp_path):
    def act(s, w, sid):
        s.seq += 1
        w.input.write(frame(GAP, s.seq, 1024))  # resumes before what was already received
    refused(tmp_path, "backwards", "audio gap runs backwards", act)


def test_a_gap_longer_than_thirty_seconds_is_still_a_fault(tmp_path):
    refused(tmp_path, "toolong", "audio gap too long", lambda s, w, sid: s.gap(16000 * 30 + 1))


def test_a_gap_out_of_sequence_is_still_a_fault(tmp_path):
    refused(tmp_path, "sequence", "audio stream/sequence differs", lambda s, w, sid: s.gap(100, seq=s.seq + 5))


def test_audio_after_the_end_is_still_a_fault(tmp_path):
    def act(s, w, sid):
        s.end(sid)
        s.pcm(1024)
    refused(tmp_path, "afterend", "input ended or discontinuous", act)


def refusal(w, sid, end):
    w.counter += 1
    w.send({"id": w.counter, "operation": "speech.session.finish_input", "arguments": {"session_id": sid, "stream_id": "capture", "end_sample": end}})
    row = w.replies.get(timeout=2)
    assert row["id"] == w.counter and "error" in row, row
    return row["error"]


def received(w, sid):
    return w.status(sid)["input"]["received_end_sample"]


def test_a_refused_finish_says_which_refusal_with_the_numbers(tmp_path):
    w = worker(tmp_path, "finish")
    try:
        w.open("finish")
        Stream(w).speech(10240)
        deadline = time.monotonic() + 5
        while received(w, "finish") < 10240:
            assert time.monotonic() < deadline, "the engine never received the audio"
            time.sleep(.01)
        assert refusal(w, "finish", 100) == "finish refused: cutoff 100 is behind the 10240 samples already received"
        # Past the listening limit the worker refuses earlier, on the argument itself.
        limit = 30 * 60 * 16000  # the default listening limit
        assert refusal(w, "finish", limit + 1) == "bounded whole number required"
        # A cutoff between what was received and the limit is still accepted, twice.
        for _ in range(2):
            w.call("finish_input", session_id="finish", stream_id="capture", end_sample=10240)
        w.call("close", session_id="finish", mode="abort")
        w.event("session_end", "finish")
    finally:
        assert w.close() == 0
