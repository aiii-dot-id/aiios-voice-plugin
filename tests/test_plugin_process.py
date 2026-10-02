"""Actual native SDK process ownership; no models, browser or audio devices."""

import json
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

from scripts.build_plugin_carrier import development_carrier, verify_build
from scripts.prove_plugin_sdk_engine import SDKHost
from tests.test_plugin_engine import open_args


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_carrier_loss_does_not_orphan_blocked_inference(tmp_path, monkeypatch):
    # Deliberately non-cancellable model call. Only the worker EOF watchdog can
    # retire it after an ungraceful carrier death; the carrier itself is gone.
    monkeypatch.setenv("AII_TEST_BLOCK_MODEL", "1")
    carrier = development_carrier()
    if selected := os.environ.get("AII_TEST_CARRIER_BUILD"):
        # A fresh source-bound test build need not replace the existing default
        # artifact. Verify the complete build before accepting this selection.
        build = Path(selected).resolve(strict=True)
        verify_build(build)
        carrier = build / carrier.name
    args = SimpleNamespace(output=tmp_path, fixture=True, carrier=carrier)
    host = SDKHost(args)
    worker = None
    record = {"passed": False, "carrier_pid": host.process.pid}
    try:
        host.call("open", open_args(), timeout=15)
        host.event("session_ready")
        children = subprocess.check_output(
            ["pgrep", "-P", str(host.process.pid)], text=True
        ).split()
        assert len(children) == 1
        worker = int(children[0])
        record["worker_pid"] = worker
        host.call(
            "synthesize",
            {"session_id": "session-one", "synthesis_id": "s1", "text": "Hold."},
        )
        deadline = time.monotonic() + 3
        while (
            "fixture inference blocked"
            not in (tmp_path / "worker.stderr.log").read_text()
        ):
            assert time.monotonic() < deadline, "model never entered blocking call"
            time.sleep(0.01)
        before = time.monotonic()
        host.process.kill()
        host.process.wait(timeout=3)
        deadline = before + 9
        while alive(worker):
            assert time.monotonic() < deadline, "worker orphaned after carrier death"
            time.sleep(0.02)
        record["worker_retired_after_seconds"] = time.monotonic() - before
        assert not any(e["type"] == "session_end" for e in host.events)
        record["passed"] = True
    finally:
        if worker is not None and alive(worker):
            os.kill(worker, 9)  # This fixture's exact recorded child, never a scan.
        record["carrier_exit"] = host.close()
        record["events"] = host.events
        (tmp_path / "carrier-loss.json").write_text(json.dumps(record, indent=2) + "\n")
