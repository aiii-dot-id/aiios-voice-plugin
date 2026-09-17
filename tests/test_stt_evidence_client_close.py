"""The development evidence client releases its child's pipes when it closes."""

import sys

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
