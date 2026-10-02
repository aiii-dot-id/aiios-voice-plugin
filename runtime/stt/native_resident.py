"""Private native STT child for the existing bounded VF102 resident client.

No public SDK change and no default selection. One model owner, a bounded
observation writer and independent input/control admission. The parent owns
the real child process and its final hard retirement bound.
"""

from __future__ import annotations

import argparse
import base64
import gc
import json
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from runtime.stt.native_async import NativeAsyncStream


class ObservedRecognizer:
    def __init__(self, inner, emit, sid):
        self.inner, self.emit, self.sid = inner, emit, sid
        self.spans = []
        self.stream = None

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def create_stream(self):
        self.stream = self.inner.create_stream()
        return self.stream

    def decode_stream(self, stream):
        start, count, _ = self.inner.window(stream)
        begin = time.perf_counter_ns()
        self.emit("encoder", sid=self.sid, decode_start_ns=begin)
        self.inner.decode_stream(stream)
        self.spans.append(
            {
                "mel_start": start,
                "mel_frames": count,
                "source_start": start * 160 - 256,
                "source_end": (start + count - 1) * 160 + 256,
                "decode_start_ns": begin,
                "decode_end_ns": time.perf_counter_ns(),
            }
        )


def terminal_record(record, native):
    spans = list(record.observed.spans)
    covered = max((s["source_end"] for s in spans), default=-1)
    token_count = len(record.observed.stream.tokens)
    samples = record.stream.ingress.samples
    facts = {
        "input_finished": bool(native.get("input_finished")),
        "features_exhausted": native.get("event") == "final"
        and native.get("decoder_ready_after_finish") is False,
        "covered_source_end": covered,
        "token_count": token_count,
        "max_new_tokens": 4096,
        "token_limit_reached": token_count >= 4096,
    }
    event = native["event"]
    if event == "final" and (
        not facts["input_finished"]
        or not facts["features_exhausted"]
        or covered < samples
        or facts["token_limit_reached"]
        or native.get("captured_samples") != samples
    ):
        event = "error"
    return {
        "event": event,
        "sid": record.sid,
        "text": native.get("text", "") if event == "final" else "",
        "outcome": "recognized"
        if event == "final" and native.get("text")
        else "no_text"
        if event == "final"
        else event,
        "admitted": samples,
        "completion": facts,
        "spans": spans,
        "native_terminal": native,
        # No Torch allocator or inferred WDDM peak-memory claim.
        "memory_observation": "not_measured_by_this_private_worker",
    }


class NativeResident:
    """Dispatch returns on admission; only the observation owner writes stdout."""

    def __init__(self, recognizer, output):
        self.recognizer, self.output = recognizer, output
        self.lock = threading.RLock()
        self.outbound = queue.Queue(maxsize=256)
        self.fault = None
        self.failed = threading.Event()
        self.active = None
        self.seen = set()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="native-model")
        self.watchers = []
        self.writer = threading.Thread(
            target=self._write, name="native-output", daemon=True
        )
        self.writer.start()

    def fail(self, error):
        with self.lock:
            if self.fault is None:
                self.fault = error
            self.failed.set()

    def emit(self, event, **fields):
        row = {"event": event, "at_ns": time.perf_counter_ns(), **fields}
        data = ("VF102 " + json.dumps(row, allow_nan=False) + "\n").encode()
        if len(data) > 65536:
            raise ValueError("Native observation exceeds frame bound")
        try:
            self.outbound.put_nowait((row, data))
        except queue.Full:
            self.fail(RuntimeError("Native observation backpressure; no silent loss"))
            raise self.fault from None

    def _write(self):
        try:
            while (item := self.outbound.get()) is not None:
                row, data = item
                if row["event"] == "partial":
                    with self.lock:
                        active = self.active
                        # A retired/cancelled generation cannot revive through a
                        # queued partial. An in-progress write precedes its ack.
                        if (
                            active is None
                            or active.sid != row["sid"]
                            or active.cancelled
                        ):
                            continue
                self.output.write(data)
                self.output.flush()
        except Exception as error:  # noqa: BLE001 - propagate pipe failure
            self.fail(error)

    def _observe(self, record):
        cursor = 0
        try:
            while True:
                cursor, rows, terminal = record.stream.observe(cursor)
                with self.lock:
                    if self.failed.is_set():
                        record.stream.cancel()
                        return
                    if not record.cancelled:
                        for row in rows:
                            self.emit(
                                "partial",
                                sid=record.sid,
                                text=row["text"],
                                admitted=record.stream.ingress.samples,
                            )
                    if terminal is not None:
                        result = terminal_record(record, terminal)
                        if record.cancelled:
                            result.update(
                                event="cancelled", text="", outcome="cancelled"
                            )
                        self.emit(**result)
                        record.terminal = True
                        return
        except Exception as error:  # noqa: BLE001 - terminal failure faults owner
            self.fail(error)

    def dispatch(self, row):
        with self.lock:
            if self.failed.is_set():
                raise RuntimeError("Native resident failed") from self.fault
            op = row["op"]
            if op == "close":
                if self.active is not None and not self.active.terminal:
                    raise ValueError("Close while recognition unresolved")
                return False
            if op == "start":
                sid = row["sid"]
                if (
                    not isinstance(sid, str)
                    or not 1 <= len(sid) <= 128
                    or sid in self.seen
                    or len(self.seen) >= 4096
                    or self.active is not None
                    and not self.active.terminal
                ):
                    raise ValueError("Duplicate, unbounded or overlapping recognition")
                self.seen.add(sid)
                observed = ObservedRecognizer(self.recognizer, self.emit, sid)
                record = SimpleNamespace(
                    sid=sid,
                    observed=observed,
                    cancelled=False,
                    terminal=False,
                    stream=NativeAsyncStream(observed, self.pool),
                )
                self.active = record
                self.emit("opened", sid=sid)
                watcher = threading.Thread(
                    target=self._observe,
                    args=(record,),
                    name="native-observation",
                    daemon=True,
                )
                # Retain only live owned watchers rather than every past turn.
                self.watchers = [t for t in self.watchers if t.is_alive()]
                self.watchers.append(watcher)
                watcher.start()
                return True
            record = self.active
            if record is None or row.get("sid") != record.sid:
                raise ValueError("Control for unknown native stream")
            if op == "cancel":
                if not record.terminal:
                    record.cancelled = True
                    record.stream.cancel()
                    self.emit("cancel_admitted", sid=record.sid)
                return True
            if record.cancelled or record.terminal:
                raise ValueError("Input after native cutoff")
            if op == "pcm":
                if (
                    type(row["start"]) is not int
                    or row["start"] != record.stream.ingress.samples
                ):
                    raise ValueError("Native input sample gap")
                values = np.frombuffer(
                    base64.b64decode(row["pcm"], validate=True), dtype="<f4"
                )
                if not 0 < len(values) <= 2048:
                    raise ValueError("Native PCM frame exceeds bounded chunk")
                record.stream.push_audio(values)
            elif op == "finish":
                record.stream.finish_input()
                self.emit(
                    "input_finished",
                    sid=record.sid,
                    admitted=record.stream.ingress.samples,
                )
            else:
                raise ValueError("Unknown native control")
            return True

    def close(self):
        if self.active is not None and not self.active.terminal:
            self.active.stream.cancel()
            self.active.stream.retire(10)
        for watcher in self.watchers:
            watcher.join(3)
            if watcher.is_alive():
                raise RuntimeError("Native observation owner did not retire")
        self.pool.shutdown(wait=True)
        if self.writer.is_alive():
            self.outbound.put(None, timeout=3)
            self.writer.join(3)
            if self.writer.is_alive():
                raise RuntimeError("Native output owner did not retire")
        if self.fault is not None:
            raise RuntimeError("Native resident observation failure") from self.fault


def serve(recognizer, input_stream, output, ready):
    resident = NativeResident(recognizer, output)
    incoming = queue.Queue(maxsize=128)

    def read():
        try:
            while True:
                raw = input_stream.readline(32769)
                if len(raw) > 32768:
                    raise ValueError("Oversized native control frame")
                row = json.loads(raw) if raw else None
                if row is not None and not isinstance(row, dict):
                    raise ValueError("Native control must be an object")
                # Bounded polling is only for owner retirement under backpressure.
                while not resident.failed.is_set():
                    try:
                        incoming.put(row, timeout=0.1)
                        break
                    except queue.Full:
                        continue
                if row is None or row.get("op") == "close" or resident.failed.is_set():
                    return
        except Exception as error:  # noqa: BLE001 - fail the owned lane
            resident.fail(error)

    reader = threading.Thread(target=read, name="native-input", daemon=True)
    try:
        resident.emit("ready", **ready)
        reader.start()
        while not resident.failed.is_set():
            try:
                row = incoming.get(timeout=0.1)
            except queue.Empty:
                continue
            if row is None or not resident.dispatch(row):
                break
        if resident.failed.is_set():
            raise RuntimeError("Native resident I/O failed") from resident.fault
    finally:
        resident.failed.set()
        resident.close()
        # Do not Close a Windows stream behind a blocked ReadFile. The process
        # owner closes its pipe and reaps the child; the reader is daemon-owned.
        reader.join(0.2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    layout = parser.add_mutually_exclusive_group(required=True)
    layout.add_argument("--native-root", type=Path)
    layout.add_argument("--model-assets", type=Path)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    if bool(args.model_assets) != bool(args.root):
        parser.error(
            "installed native loading requires --model-assets and --root together"
        )
    if sys.platform != "win32":
        raise RuntimeError("This candidate requires explicit Windows DirectML")
    from runtime.stt.nemotron_onnx import NemoOnnxRecognizer
    from runtime.model_assets import ModelAssets
    from runtime.stt.native_assets import load_installed, load_qualification

    begin = time.perf_counter()
    bound = (
        load_installed(
            ModelAssets(args.model_assets, args.root, backend="windows-pocket")
        )
        if args.model_assets
        else load_qualification(args.native_root)
    )
    verified = time.perf_counter()
    recognizer = NemoOnnxRecognizer(
        bound.model,
        bound.mel,
        aligned_encoder=bound.encoder_path,
        encoder_provider="DmlExecutionProvider",
        external_initializers=bound.encoder_path == bound.model / "encoder.onnx",
    )
    try:
        serve(
            recognizer,
            sys.stdin.buffer,
            sys.stdout.buffer,
            {
                "backend": "native-directml",
                **bound.identity,
                "providers": {
                    k: getattr(recognizer, k).get_providers()
                    for k in ("encoder", "decoder", "joiner")
                },
                "model_dtype": "float32",
                "max_input_seconds": 60,
                "observation_clock": "perf_counter_ns",
                "observation_clock_resolution_seconds": time.get_clock_info(
                    "perf_counter"
                ).resolution,
                "memory_observation": "not_measured_by_this_private_worker",
                "startup_seconds": {
                    "verification": verified - begin,
                    "construction": time.perf_counter() - verified,
                    "total": time.perf_counter() - begin,
                },
            },
        )
    finally:
        recognizer.close()
        gc.collect()


if __name__ == "__main__":
    main()
