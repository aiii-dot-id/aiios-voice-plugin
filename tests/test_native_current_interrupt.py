"""Actual native worker dispatcher with deterministic models, not acoustics."""
import os
from pathlib import Path

import pytest

from scripts.prove_native_worker_transport import Worker


@pytest.fixture
def worker(tmp_path):
    binary = Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]).resolve()
    w = Worker(binary, tmp_path / "worker")
    try:
        w.open("session")
        yield w
    finally:
        if w.p.poll() is None:
            w.call("close", session_id="session", mode="abort")
            w.event("session_end", "session")
        assert w.close() == 0


@pytest.mark.parametrize("selector", [{}, {"synthesis_id": None}, {"synthesis_id": ""}, {"synthesis_id": "active"}])
def test_current_interruption_does_not_wait_for_inference(worker, selector):
    worker.call("synthesize", session_id="session", synthesis_id="active", text="Hold.")
    worker.event("synthesis_start", "session")
    stopped, stop_time = worker.call("stop_playback", session_id="session", **selector)
    assert stopped["synthesis_id"] == "active" and stopped["output_fenced"]
    assert worker.status("session")["synthesis"]["state"] == "running", "stop must not cancel computation"
    cancelled, cancel_time = worker.call("cancel_synthesis", session_id="session", **selector)
    assert cancelled["synthesis_id"] == "active"
    worker.event("synthesis_cancelled", "session")
    assert stop_time < 1 and cancel_time < 1
    worker.call("playback_report", session_id="session", synthesis_id="active", output_stream=stopped["output_stream"], rendered_samples=0, terminal=True)
    # New synthesis remains usable after the interrupted one retires.
    worker.call("synthesize", session_id="session", synthesis_id="recovery", text="Recovery.")
    worker.event("synthesis_end", "session")


def refuse(worker, operation, **arguments):
    worker.counter += 1
    worker.send({"id": worker.counter, "operation": "speech.session." + operation,
                 "arguments": {"session_id": "session", **arguments}})
    row = worker.replies.get(timeout=2)
    assert row["id"] == worker.counter and "error" in row, row


@pytest.mark.parametrize("selector", [False, 0, [], {}, "foreign", "x" * 257])
def test_invalid_or_unknown_selector_never_interrupts_current(worker, selector):
    worker.call("synthesize", session_id="session", synthesis_id="active", text="Hold.")
    worker.event("synthesis_start", "session")
    refuse(worker, "stop_playback", synthesis_id=selector)
    refuse(worker, "cancel_synthesis", synthesis_id=selector)
    assert worker.status("session")["synthesis"]["state"] == "running"
    assert not any(e["type"] == "interruption_requested" for e in worker.events)


@pytest.mark.parametrize("operation", ["stop_playback", "cancel_synthesis"])
def test_idle_current_interruption_is_admitted(worker, operation):
    value, elapsed = worker.call(operation, session_id="session", synthesis_id="")
    assert value["synthesis_id"] is None and value["output_stream"] is None
    assert elapsed < 1


@pytest.mark.parametrize("selector", [{}, {"synthesis_id": None}, {"synthesis_id": ""}])
def test_playback_receipt_still_requires_explicit_synthesis_identity(worker, selector):
    worker.call("synthesize", session_id="session", synthesis_id="active", text="Hold.")
    worker.event("synthesis_start", "session")
    worker.call("stop_playback", session_id="session", synthesis_id="active")
    refuse(worker, "playback_report", output_stream=1, rendered_samples=0, terminal=True, **selector)
    assert worker.status("session")["synthesis"]["state"] == "running"
