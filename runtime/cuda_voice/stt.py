"""Bounded resident RNN-T client; no model work on the caller's control lane."""

import base64
import json
import queue
import subprocess
import threading
import time
import uuid
from collections import deque
from types import SimpleNamespace

import numpy as np


class ResidentSTT:
    def __init__(
        self,
        command,
        log,
        *,
        env=None,
        wait_ready=True,
        separate_stderr=False,
        graceful_close=False,
    ):
        self.condition = threading.Condition()
        self.commands = queue.Queue(maxsize=128)
        self.active = self.ready = self.failure = None
        self.stopped = False
        self.graceful_close = graceful_close
        self.retirement = None
        self.startup_deadline = time.monotonic() + 90
        self.log = log.open("xb") if log is not None else None
        self.diagnostics = deque(maxlen=64)
        self.diagnostic_bytes = 0
        self.stderr_reader = None
        try:
            self.child = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE if separate_stderr else subprocess.STDOUT,
                env=env,
            )
        except BaseException:
            if self.log is not None:
                self.log.close()
            raise
        self.writer = threading.Thread(target=self._write, daemon=True)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.writer.start()
        self.reader.start()
        if separate_stderr:
            self.stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
            self.stderr_reader.start()
        if wait_ready:
            self.await_ready()

    def await_ready(self, seconds=90):
        """Join model readiness; optional launch-only construction owns the child.

        Windows may load its independent CPU synthesizer while this process
        loads CUDA STT. No audio is admitted until readiness, and delaying this
        join never extends the original startup deadline. Default construction
        retains the synchronous readiness contract for all existing callers.
        """
        try:
            with self.condition:
                self._wait(
                    lambda: self.ready is not None,
                    min(seconds, max(0, self.startup_deadline - time.monotonic())),
                )
                return self.ready
        except BaseException as original:
            try:
                self.close()
            except BaseException as retirement:
                self._startup_note(original)
                raise BaseExceptionGroup(
                    "recognizer startup and retirement failed", [original, retirement]
                ) from None
            self._startup_note(original)
            raise

    def _startup_note(self, error):
        # Read only after bounded retirement drained stderr. Diagnostic bytes
        # never enter the protocol and cannot replace the original exception.
        with self.condition:
            tail = b"".join(self.diagnostics)[-8192:]
        text = tail.decode("utf-8", errors="backslashreplace").replace("\x00", "\\0")
        error.add_note(
            f"recognizer retirement={self.retirement}; stderr tail (bounded): {text}"
        )

    def _fault(self, error):
        with self.condition:
            if self.failure is None:
                self.failure = str(error)
            self.condition.notify_all()

    def _wait(self, predicate, seconds):
        deadline = time.monotonic() + seconds
        while not predicate():
            if self.stopped:
                raise RuntimeError("STT worker was closed")
            if self.failure:
                raise RuntimeError("STT worker: " + self.failure)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("STT worker did not resolve within its bound")
            self.condition.wait(remaining)
        if self.stopped:
            raise RuntimeError("STT worker was closed")
        if self.failure:
            raise RuntimeError("STT worker: " + self.failure)

    def send(self, op, **fields):
        if self.failure or self.stopped:
            raise RuntimeError("STT worker is unavailable")
        data = (json.dumps({"op": op, **fields}, allow_nan=False) + "\n").encode()
        if len(data) > 32768:
            raise ValueError("STT command exceeds worker frame limit")
        try:
            self.commands.put_nowait(data)
        except queue.Full:
            self._fault("STT command queue full; no input silently dropped")
            raise RuntimeError(self.failure) from None

    def _write(self):
        try:
            while (data := self.commands.get()) is not None:
                self.child.stdin.write(data)
                self.child.stdin.flush()
        except Exception as exc:  # noqa: BLE001 - propagate pipe failure
            if not self.stopped:
                self._fault(exc)

    def _read(self):
        try:
            while raw := self.child.stdout.readline(65537):
                if len(raw) > 65536:
                    raise RuntimeError("oversized STT observation")
                if self.log is not None:
                    self.log.write(raw)
                    self.log.flush()
                if not raw.startswith(b"VF102 "):
                    continue
                row = json.loads(raw[6:])
                with self.condition:
                    kind = row["event"]
                    if kind == "ready":
                        if self.ready is not None:
                            raise RuntimeError("duplicate STT readiness")
                        self.ready = row
                    else:
                        stream = self.active
                        if stream is None or row.get("sid") != stream.sid:
                            raise RuntimeError("foreign STT stream observation")
                        if kind == "partial":
                            stream.text = row["text"]
                        elif kind in {"final", "cancelled", "error"}:
                            if stream.terminal is not None:
                                raise RuntimeError("duplicate STT terminal")
                            stream.terminal = row
                    self.condition.notify_all()
        except Exception as exc:  # noqa: BLE001 - malformed/failed worker faults client
            self._fault(exc)
        finally:
            if not self.stopped:
                self._fault("recognizer process exited")
            if self.log is not None:
                self.log.close()

    def _read_stderr(self):
        """Native diagnostics are bytes, never part of the UTF-8 protocol.

        Windows libraries can emit UTF-16 stderr. Keep a bounded diagnostic
        tail while draining the whole pipe; no system logging or code changes
        are needed in the dependency, and no bytes can poison a ready frame.
        """
        try:
            while chunk := self.child.stderr.read(4096):
                with self.condition:
                    self.diagnostic_bytes += len(chunk)
                    self.diagnostics.append(chunk)
        except Exception as exc:  # noqa: BLE001 - report actual pipe failure
            if not self.stopped:
                self._fault(exc)

    def stream(self):
        with self.condition:
            if self.ready is None or self.stopped:
                raise RuntimeError("STT worker is not ready")
            if self.active is not None and self.active.terminal is None:
                raise RuntimeError("prior recognition has not retired")
            self.active = Stream(self)
            self.send("start", sid=self.active.sid)
            return self.active

    def cancel(self):
        with self.condition:
            stream = self.active
            if stream is not None and stream.terminal is None and not stream.cancelled:
                stream.cancelled = True
                self.send("cancel", sid=stream.sid)

    def retire(self, seconds=15):
        with self.condition:
            self._wait(
                lambda: self.active is None or self.active.terminal is not None, seconds
            )

    def close(self):
        requested = False
        with self.condition:
            if (
                self.graceful_close
                and not self.stopped
                and not self.failure
                and self.ready is not None
                and self.child.poll() is None
            ):
                try:
                    self.send("close")
                    requested = True
                except RuntimeError:
                    pass  # Saturated/faulted admission requires actual retirement.
            self.stopped = True
            self.condition.notify_all()
        mode = "already_exited"
        if requested:
            mode = "graceful"
            try:
                self.child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                mode = "graceful_timeout_terminated"
        if self.child.poll() is None:
            if not requested:
                mode = "terminated"
            self.child.terminate()
            try:
                self.child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=5)
                raise RuntimeError("recognizer required force kill") from None
        # A dead reader closes the pipe before the writer is joined.
        try:
            self.commands.put_nowait(None)
        except queue.Full:
            while not self.commands.empty():
                self.commands.get_nowait()
            self.commands.put_nowait(None)
        self.reader.join(timeout=3)
        self.writer.join(timeout=3)
        if self.stderr_reader is not None:
            self.stderr_reader.join(timeout=3)
        if (
            self.reader.is_alive()
            or self.writer.is_alive()
            or self.stderr_reader is not None
            and self.stderr_reader.is_alive()
        ):
            raise RuntimeError("recognizer IPC thread remained alive")
        self.child.stdin.close()
        self.child.stdout.close()
        if self.stderr_reader is not None:
            self.child.stderr.close()
        self.retirement = {"mode": mode, "exit_code": self.child.poll()}
        if requested and (mode != "graceful" or self.child.poll() != 0):
            raise RuntimeError(
                "native recognizer did not close cleanly: " + str(self.retirement)
            )


class Stream:
    def __init__(self, owner):
        self.owner = owner
        self.sid = uuid.uuid4().hex
        self.samples = 0
        self.text = ""
        self.terminal = None
        self.finished = self.cancelled = False

    @staticmethod
    def update(text):
        return [SimpleNamespace(result=SimpleNamespace(text=text))]

    def push_audio(self, pcm):
        values = np.asarray(pcm, dtype="<f4")
        if (
            values.ndim != 1
            or not len(values)
            or not np.isfinite(values).all()
            or self.samples + len(values) > 16000 * 60
        ):
            raise ValueError("STT requires finite mono audio within 60 seconds")
        with self.owner.condition:
            if self.finished or self.cancelled or self.terminal is not None:
                raise RuntimeError("audio after recognizer cutoff")
            for start in range(0, len(values), 2048):
                chunk = values[start : start + 2048]
                self.owner.send(
                    "pcm",
                    sid=self.sid,
                    start=self.samples,
                    pcm=base64.b64encode(chunk.tobytes()).decode(),
                )
                self.samples += len(chunk)
            return self.update(self.text)

    def finish(self):
        with self.owner.condition:
            if self.finished or self.cancelled:
                raise RuntimeError("duplicate or cancelled recognition finish")
            self.finished = True
            self.owner.send("finish", sid=self.sid)
            self.owner._wait(lambda: self.terminal is not None, 20)
            row = self.terminal
            facts = row.get("completion", {})
            if (
                row["event"] != "final"
                or row["admitted"] != self.samples
                or not facts.get("input_finished")
                or not facts.get("features_exhausted")
                or facts.get("token_limit_reached")
                or facts.get("covered_source_end", -1) < self.samples
            ):
                raise RuntimeError(
                    "recognition did not consume the exact input tail: " + str(row)
                )
            return self.update(row["text"])
