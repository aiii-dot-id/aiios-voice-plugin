"""The development evidence client releases its child's pipes when it closes."""

import json
import os
import signal
import sys
import threading
import time

from runtime.stt.evidence_client import EvidenceClient

FAKE_WORKER = """import json, sys
print('VF102 {"event": "ready"}', flush=True)
for line in sys.stdin:
    if json.loads(line)["op"] == "close":
        break
"""


def test_close_releases_the_child_pipes(tmp_path):
    worker = tmp_path / "fake_worker.py"
    worker.write_text(FAKE_WORKER)
    client = EvidenceClient(sys.executable, worker, tmp_path, tmp_path / "worker.log")
    client.close()
    assert client.child.returncode == 0
    assert client.child.stdin.closed and client.child.stdout.closed
    assert not client.reader.is_alive()


def test_bounded_record_is_json_safe_and_names_dropped_events(tmp_path):
    worker = tmp_path / "many_events.py"
    worker.write_text(FAKE_WORKER.replace(
        "for line in sys.stdin:",
        "for n in range(5000):\n    print('VF102 ' + json.dumps({'event': 'partial', 'n': n}), flush=True)\nfor line in sys.stdin:",
    ))
    client = EvidenceClient(sys.executable, worker, tmp_path, tmp_path / "worker.log")
    try:
        client.close()
        record = json.loads(json.dumps(client.record()))
        assert len(record["events"]) == 4096
        assert record["events_dropped"] == 5001 - 4096
        assert record["events"][-1]["n"] == 4999
        assert json.loads(json.dumps(client.messages)) == record["events"]
    finally:
        client.close(abort=True)


HELD_WORKER = """import json, subprocess, sys
# A descendant inherits stdout and keeps the pipe open after this worker exits.
held = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
print('VF102 {"event": "ready"}', flush=True)
print('VF102 ' + json.dumps({"event": "held", "pid": held.pid}), flush=True)
for line in sys.stdin:
    if json.loads(line)["op"] == "close":
        break
"""


def test_close_is_bounded_when_a_descendant_holds_stdout_open(tmp_path):
    worker = tmp_path / "held_worker.py"
    worker.write_text(HELD_WORKER)
    client = EvidenceClient(sys.executable, worker, tmp_path, tmp_path / "worker.log")
    held = client.events.get(timeout=10)
    assert held["event"] == "held"
    closing = threading.Thread(target=client.close, daemon=True)
    try:
        started = time.monotonic()
        closing.start()
        closing.join(timeout=15)
        assert not closing.is_alive(), "close() waited on the reader blocked in stdout"
        assert time.monotonic() - started < 15
        assert client.child.returncode == 0
        assert client.child.stdin.closed
        assert client.reader.is_alive()  # the descendant still holds the pipe
    finally:
        os.kill(held["pid"], signal.SIGKILL)
        closing.join(timeout=10)
    client.reader.join(timeout=10)
    assert not client.reader.is_alive()
    assert client.child.stdout.closed
