"""Real worker/core, deterministic models. Playback reports are not acoustic proof."""
import os
import time
from pathlib import Path

import pytest
from scripts.prove_native_worker_transport import Worker


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
            assert ("error" in row) == (traffic == "foreign")
            time.sleep(.25)
        failure = w.event("failure", "drain")
        assert "no progress" in failure["reason"]
        assert 14 <= time.monotonic() - start < 19
        assert failure["resources_released"] and not failure["playback_verified"]
    finally:
        assert w.close() != 0
