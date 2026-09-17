"""The worker process exits non-zero when its cleanup reports errors.

The carrier joins the worker's exit status, so a synthesis fault that only
surfaces while the engine is released must not leave the process as a success,
and must not replace an error that was already ending the worker.
"""

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

from tests.plugin_models import open_args

ROOT = Path(__file__).resolve().parents[1]


class Worker:
    """tests/plugin_worker_fixture.py over its private pipes and audio descriptors."""

    def __init__(self, **env):
        audio_in, self.audio_in = os.pipe()
        self.audio_out, audio_out = os.pipe()
        self.child = subprocess.Popen(
            [sys.executable, "-m", "tests.plugin_worker_fixture"],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                **os.environ,
                "AII_AUDIO_IN_FD": str(audio_in),
                "AII_AUDIO_OUT_FD": str(audio_out),
                **env,
            },
            pass_fds=(audio_in, audio_out),
        )
        os.close(audio_in)
        os.close(audio_out)
        self.lines = queue.Queue()
        threading.Thread(target=self.read, daemon=True).start()
        threading.Thread(target=self.drain, daemon=True).start()

    def read(self):
        for line in self.child.stdout:
            self.lines.put(json.loads(line))
        self.lines.put(None)

    def drain(self):
        while os.read(self.audio_out, 65536):
            pass

    def send(self, body):
        self.child.stdin.write(json.dumps(body).encode() + b"\n")
        self.child.stdin.flush()

    def until(self, predicate):
        while True:
            message = self.lines.get(timeout=10)
            assert message is not None, "worker output ended early"
            if predicate(message):
                return message

    def event(self, kind):
        return self.until(lambda m: m.get("event", {}).get("type") == kind)["event"]

    def speak(self):
        self.until(lambda m: "ready" in m)
        self.send({"id": 1, "operation": "speech.session.open", "arguments": open_args()})
        self.event("session_ready")
        self.send(
            {
                "id": 2,
                "operation": "speech.session.synthesize",
                "arguments": {"session_id": "session-one", "synthesis_id": "s1", "text": "A reply."},
            }
        )

    def exit(self):
        """Close the private control pipe, as carrier death does, and join the worker."""
        self.child.stdin.close()
        try:
            code = self.child.wait(timeout=20)
        finally:
            if self.child.poll() is None:
                self.child.kill()
                self.child.wait()
            os.close(self.audio_in)
        return code, self.child.stderr.read().decode()


def test_a_synthesis_fault_found_at_release_fails_the_worker():
    worker = Worker(AII_TEST_WRONG_CLOCK="1")
    try:
        worker.speak()
        failure = worker.event("failure")
        assert "synthesis failed" in failure["reason"], failure
    finally:
        code, stderr = worker.exit()
    assert "worker cleanup: synthesis failed" in stderr, stderr
    assert code != 0, stderr
    assert stderr.rstrip().splitlines()[-1].startswith("RuntimeError: worker cleanup failed"), stderr


def test_a_clean_release_still_exits_zero():
    worker = Worker()
    try:
        worker.speak()
        worker.event("synthesis_end")
    finally:
        code, stderr = worker.exit()
    assert "worker cleanup" not in stderr, stderr
    assert code == 0, stderr


def test_cleanup_errors_do_not_replace_the_error_already_ending_the_worker():
    worker = Worker(AII_TEST_WRONG_CLOCK="1")
    try:
        worker.speak()
        worker.event("failure")
        worker.send({"id": "not-an-integer", "operation": "speech.session.status"})
    finally:
        code, stderr = worker.exit()
    assert "worker cleanup: synthesis failed" in stderr, stderr
    assert code != 0, stderr
    assert stderr.rstrip().splitlines()[-1] == "ValueError: malformed private request", stderr
