"""Synthetic client observations for SDK tests, never actual browser rendering."""

import time

from runtime.plugin_engine.audio import END


def receipt(host, session_id, terminal_event, *, stopped=False, retry=False):
    stream = terminal_event["output_stream"]
    delivered = terminal_event["delivered_samples"]
    # The control notification may beat the independent audio reader. A
    # simulated sink cannot report bytes until its audio plane has seen them.
    deadline = time.monotonic() + 5
    while not any(f.kind == END and f.stream == stream for _, f in host.frames):
        if time.monotonic() >= deadline:
            raise TimeoutError("receipt sink has not received stream END")
        time.sleep(0.002)
    end = next(f for _, f in host.frames if f.kind == END and f.stream == stream)
    assert end.start == delivered
    rendered = delivered // 3 if stopped else delivered
    arguments = {
        "session_id": session_id,
        "synthesis_id": terminal_event["synthesis_id"],
        "output_stream": stream,
        "rendered_samples": rendered,
        "terminal": True,
    }
    result = host.call("playback_report", arguments)
    assert result == {
        "accepted": True,
        **{k: v for k, v in arguments.items() if k != "session_id"},
    }
    observed = host.event(
        "playback_observation", terminal_event["synthesis_id"], session_id=session_id
    )
    assert observed["rendered_samples"] == rendered
    assert observed["terminal"] is True
    assert observed["outcome"] == ("stopped" if stopped else "drained")
    assert observed["playback_verified"] is False
    if retry:
        snapshot = host.call("status", {"session_id": session_id})
        assert host.call("playback_report", arguments) == result
        assert host.call("status", {"session_id": session_id}) == snapshot
    return {
        "origin": "simulated client report after test sink receives END",
        "arguments": arguments,
        "result": result,
        "observation": observed,
        "exact_terminal_retry": retry,
    }
