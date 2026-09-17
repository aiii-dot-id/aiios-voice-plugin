"""SDK Abort during an unresolved drain; pipe evidence, never browser silence."""

import hashlib
import time
from pathlib import Path

from runtime.plugin_engine.audio import END, PCM, Frame


def abort_waiting_for_receipt(host, open_arguments, session_id="abort-during-drain"):
    event_start, call_start = len(host.events), len(host.calls)
    host.call("open", dict(open_arguments, session_id=session_id))
    host.event("session_ready", session_id=session_id)
    synth = session_id + "-speech"
    host.call(
        "synthesize",
        {
            "session_id": session_id,
            "synthesis_id": synth,
            "text": "This reply has finished synthesis but still awaits playback.",
        },
    )
    end = host.event("synthesis_end", synth, session_id=session_id)
    stream = end["output_stream"]
    deadline = time.monotonic() + 5
    while not any(f.kind == END and f.stream == stream for _, f in host.frames):
        if time.monotonic() >= deadline:
            raise TimeoutError("Abort proof did not receive the output tail")
        time.sleep(0.002)
    frames = [f for _, f in host.frames if f.stream == stream]
    pcm = b"".join(f.pcm for f in frames if f.kind == PCM)
    assert len(pcm) // 2 == frames[-1].start == end["delivered_samples"] > 0
    pcm_path = Path(host.log.name).parent / "abort-drain-output.pcm"
    with pcm_path.open("xb") as target:
        target.write(pcm)
    host.call(
        "finish_input", {"session_id": session_id, "stream_id": "mic", "end_sample": 0}
    )
    host.to_engine.write(Frame(END, 7, 1, 0).encode())
    host.event("input_finished", session_id=session_id)
    host.call("close", {"session_id": session_id, "mode": "drain"})
    waiting = host.call("status", {"session_id": session_id})
    assert waiting["lifecycle"] == "draining"
    assert waiting["playback"]["queued_samples"] == len(pcm) // 2
    assert not any(
        x["type"] == "session_end" and x["session_id"] == session_id
        for x in host.events[event_start:]
    )
    assert host.call("close", {"session_id": session_id, "mode": "abort"}) == {
        "accepted": True,
        "mode": "abort",
    }
    abort_call = host.calls[-1]
    terminal = host.event("session_end", session_id=session_id, timeout=5)
    final = host.call("status", {"session_id": session_id})
    assert terminal["status"] == "aborted" and terminal["playback_verified"] is False
    assert final["lifecycle"] == "closed"
    assert final["playback"] == waiting["playback"], "Abort invented render evidence"
    assert not any(
        x["type"] == "playback_observation" for x in host.events[event_start:]
    )
    return {
        "scope": "real SDK drain-to-abort; playback unobserved, not physical silence",
        "session_id": session_id,
        "synthesis_id": synth,
        "output_stream": stream,
        "delivered_samples": len(pcm) // 2,
        "pcm_file": pcm_path.name,
        "pcm_sha256": hashlib.sha256(pcm).hexdigest(),
        "frames": [
            {
                "kind": f.kind,
                "stream": f.stream,
                "sequence": f.seq,
                "start_sample": f.start,
                "samples": len(f.pcm) // 2,
            }
            for f in frames
        ],
        "abort_admission_seconds": abort_call["seconds"],
        "waiting_snapshot": waiting,
        "final_snapshot": final,
        "events": host.events[event_start:],
        "calls": host.calls[call_start:],
    }
