"""Production worker lifecycle/resource fences; deterministic models, not a soak."""
import json
import os
import subprocess
import time
from pathlib import Path

from scripts.prove_native_worker_transport import Worker


def until(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "observation deadline"
        time.sleep(.001)


def raw_refusal(w, op, **arguments):
    w.counter += 1
    w.send({"id": w.counter, "operation": "speech.session." + op, "arguments": arguments})
    row = w.replies.get(timeout=2)
    assert row["id"] == w.counter and "error" in row, row


def finish_reply(w, sid, name, receipt=True):
    start = len(w.frames)
    value, _ = w.call("synthesize", session_id=sid, synthesis_id=name, text="Recovery.")
    stream = value["output_stream"]
    until(lambda: any(f["kind"] == 3 and f["stream"] == stream for f in w.frames[start:]))
    count = sum(f["samples"] for f in w.frames[start:] if f["kind"] == 1 and f["stream"] == stream)
    args = dict(session_id=sid, synthesis_id=name, output_stream=stream, rendered_samples=count, terminal=True)
    if receipt:
        w.call("playback_report", **args)
    return args


def idle_sample(pid):
    def read():
        output = subprocess.check_output(["ps", "-p", str(pid), "-o", "time=", "-o", "rss="], text=True).split()
        parts = list(map(float, output[0].split(":")))
        return sum(v * 60 ** i for i, v in enumerate(reversed(parts))), int(output[1])
    before, _ = read(); started = time.monotonic()
    time.sleep(2)
    after, rss = read(); elapsed = time.monotonic() - started
    return dict(cpu_seconds=after-before, wall_seconds=elapsed, one_core_percent=100*(after-before)/elapsed, rss_kib=rss)


def test_more_than_1024_sessions_do_not_exhaust_the_activation(tmp_path):
    w = Worker(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]), tmp_path / "sessions")
    try:
        for index in range(1050):
            sid = f"session-{index}"
            w.open(sid)
            w.call("close", session_id=sid, mode="abort")
            w.event("session_end", sid)
        raw_refusal(w, "open", session_id="session-0")
        assert not any(e["type"] == "failure" for e in w.events)
    finally:
        assert w.close() == 0


def test_more_than_4096_replies_reclaim_jobs_but_keep_receipt_fences(tmp_path):
    w = Worker(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]), tmp_path / "replies")
    try:
        query = w.open("long", False)
        w.send({"settings_reply": {**query, "values": {"capture_limit_minutes": 0}}})
        w.event("session_ready", "long")
        first = finish_reply(w, "long", "reply-0")
        baseline = idle_sample(w.p.pid)
        for index in range(1, 4100):
            finish_reply(w, "long", f"reply-{index}")
        until(lambda: w.status("long")["bookkeeping"]["unresolved_generations"] == 0)
        state = w.status("long")
        assert state["bookkeeping"]["identity_fences"] == 4100
        assert state["playback"]["delivered_samples"] == state["playback"]["rendered_samples"]
        (tmp_path / "idle-history.json").write_text(json.dumps(dict(one_reply=baseline, after_4100=idle_sample(w.p.pid), state=state), indent=2))
        # An arbitrarily old exact duplicate remains idempotent. A changed
        # receipt/reused ID cannot acquire the newest generation's authority.
        w.call("playback_report", **first)
        raw_refusal(w, "playback_report", **{**first, "rendered_samples": 0})
        raw_refusal(w, "synthesize", session_id="long", synthesis_id="reply-0", text="Replay.")
        w.call("synthesize", session_id="long", synthesis_id="held", text="Hold.")
        started = time.monotonic()
        w.call("stop_playback", session_id="long", synthesis_id="held")
        w.call("cancel_synthesis", session_id="long", synthesis_id="held")
        assert time.monotonic() - started < 1
        w.call("close", session_id="long", mode="abort"); w.event("session_end", "long")
        w.open("replacement")
        raw_refusal(w, "playback_report", **first)
        raw_refusal(w, "synthesize", session_id="replacement", synthesis_id="reply-0", text="Reuse.")
        finish_reply(w, "replacement", "fresh")
        w.call("close", session_id="replacement", mode="abort"); w.event("session_end", "replacement")
    finally:
        assert w.close() == 0


def test_unresolved_receipt_custody_is_bounded_not_evicted(tmp_path):
    w = Worker(Path(os.environ["AII_NATIVE_INTERRUPT_FIXTURE"]), tmp_path / "pending")
    try:
        w.open("pending")
        receipts = [finish_reply(w, "pending", f"pending-{i}", False) for i in range(64)]
        raw_refusal(w, "synthesize", session_id="pending", synthesis_id="overflow", text="Overflow.")
        assert w.status("pending")["bookkeeping"]["unresolved_generations"] == 64
        w.call("playback_report", **receipts[0])
        until(lambda: w.status("pending")["bookkeeping"]["unresolved_generations"] == 63)
        finish_reply(w, "pending", "now-admitted")
        for receipt in receipts[1:]: w.call("playback_report", **receipt)
        w.call("finish_input", session_id="pending", stream_id="capture", end_sample=0)
        w.call("close", session_id="pending", mode="drain")
        assert w.event("session_end", "pending")["status"] == "completed"
    finally:
        assert w.close() == 0
