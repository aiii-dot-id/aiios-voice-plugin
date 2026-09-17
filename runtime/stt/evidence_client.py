"""Single-owner client for the pinned development CUDA worker, not SDK wire."""

import base64
import json
import queue
import subprocess
import threading
import time
from collections import deque

import numpy as np


class EvidenceClient:
    def __init__(self, python, worker, root, logfile, *, worker_args=()):
        self.child = subprocess.Popen(
            [str(python), str(worker), "--root", str(root), *worker_args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self.events = queue.Queue()
        self.messages = deque(maxlen=4096)  # a bounded record, not a log

        def read():
            try:
                with logfile.open("x") as log:
                    for line in self.child.stdout:
                        log.write(line)
                        log.flush()
                        if line.startswith("VF102 "):
                            row = json.loads(line[6:])
                            self.messages.append(row)
                            self.events.put(row)
            except Exception as exc:  # noqa: BLE001 - propagate protocol failure
                self.events.put({"event": "protocol_error", "message": repr(exc)})
            finally:
                self.events.put({"event": "exited"})

        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        try:
            self.ready = self.events.get(timeout=60)
            if self.ready["event"] != "ready":
                raise RuntimeError(self.ready)
        except BaseException:
            self.close(abort=True)
            raise

    def send(self, op, **fields):
        self.child.stdin.write(json.dumps({"op": op, **fields}) + "\n")
        self.child.stdin.flush()

    def recognize(self, sid, pcm):
        if (
            pcm.ndim != 1
            or not len(pcm)
            or len(pcm) > 16000 * 12
            or not np.isfinite(pcm).all()
        ):
            raise ValueError("bounded finite recognition PCM required")
        self.send("start", sid=sid)
        for start in range(0, len(pcm), 512):
            self.send(
                "pcm",
                sid=sid,
                start=start,
                pcm=base64.b64encode(
                    pcm[start : start + 512].astype("<f4").tobytes()
                ).decode(),
            )
            time.sleep(0.032)
        self.send("finish", sid=sid)
        deadline = time.monotonic() + 20
        while True:
            row = self.events.get(timeout=max(0.001, deadline - time.monotonic()))
            if row["event"] in ("exited", "protocol_error", "cancelled"):
                raise RuntimeError(row)
            if row.get("sid", sid) != sid:
                raise ValueError("worker returned another session")
            if row["event"] in ("final", "error"):
                if row["event"] == "final" and (
                    row["admitted"] != len(pcm)
                    or not row["spans"]
                    or row["spans"][-1]["source_end"] < len(pcm)
                ):
                    raise ValueError("recognition tail incomplete")
                return row

    def close(self, abort=False):
        try:
            if self.child.poll() is None:
                if abort:
                    self.child.terminate()
                else:
                    self.send("close")
                try:
                    code = self.child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.child.kill()
                    self.child.wait(timeout=5)
                    raise RuntimeError("worker close timed out") from None
                if code and not abort:
                    raise RuntimeError("worker close failed")
        finally:
            self.reader.join(timeout=3)
            # The pipes are ours to release whether or not the reader retired.
            for pipe in (self.child.stdin, self.child.stdout):
                if pipe is not None:
                    try:
                        pipe.close()
                    except OSError:
                        pass
