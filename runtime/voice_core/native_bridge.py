"""Native CoreAudio owns both devices; the browser carries controls/status only."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from .control_vad import ControlVAD
from .evidence_io import EventSpool, file_identity, finalize_off_loop
from .live import LiveSession
from .native_stream import NativePCM


class NativeBridge:
    def __init__(
        self,
        root,
        binding,
        models,
        executor,
        send,
        evidence,
        reply,
        input_uid,
        output_uid,
    ):
        from scripts.probe_macos_audio_host import identity

        if identity() != binding:
            raise ValueError("native host binding changed since server start")
        self.root, self.binding, self.send, self.evidence = (
            root,
            binding,
            send,
            evidence,
        )
        self.input_uid, self.output_uid = input_uid, output_uid
        self.control = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="voice-control"
        )
        self.session = LiveSession(
            models, executor, self.model_message, evidence, reply, precomputed_vad=True
        )
        self.vad = self.pcm = self.process = self.reader = self.worker = None
        self.fd = None
        self.control_fd = None
        self.temporary = None
        self.send_lock = asyncio.Lock()
        self.control_send_lock = asyncio.Lock()
        self.input_lock = asyncio.Lock()
        self.ending = False
        self.end_requested_at = None
        self.model_input_done = False
        self.finishing_host = False
        self.native_active = set()
        self.sent_audio = set()
        self.cancelled = set()
        self.probabilities = deque()
        self.fast_position = self.admitted_position = 0
        self.native_report = None
        self.journal = EventSpool(evidence.path / "bridge-events.jsonl")
        self.closed = False
        self.input_cutoff = None
        self.capture_ready = False
        self.last_admitted_at = None
        self.ready_at = None
        self.input_limit_seconds = evidence.meta["settings"].get(
            "max_session_seconds", 1800
        )
        if (
            type(self.input_limit_seconds) is not int
            or not 1 <= self.input_limit_seconds <= 1800
        ):
            raise ValueError("max_session_seconds must be an integer within 1..1800")
        self.host_limit_seconds = self.input_limit_seconds + 60
        self.last_resource_sample = 0

    def record(self, kind, **fields):
        row = dict(type=kind, observed_monotonic_ns=time.monotonic_ns(), **fields)
        self.journal.append(row)
        return row

    async def command(self, row):
        if self.fd is None or self.native_report is not None:
            raise RuntimeError("native command receiver is not running")
        raw = (json.dumps(row, separators=(",", ":")) + "\n").encode()
        urgent = row["type"] in {"cancel", "stop"} and self.control_fd is not None
        fd = self.control_fd if urgent else self.fd
        async with self.control_send_lock if urgent else self.send_lock:
            if row["type"] == "audio" and row["synthesis_id"] in self.cancelled:
                return
            start = time.monotonic_ns()
            offset = 0
            deadline = time.monotonic() + 2
            while offset < len(raw):
                if time.monotonic() > deadline:
                    raise TimeoutError("native control pipe exceeded two seconds")
                try:
                    offset += os.write(fd, raw[offset:])
                except BlockingIOError:
                    await asyncio.sleep(0.001)
            self.record(
                "native_command",
                command={k: v for k, v in row.items() if k != "pcm_f32le"},
                send_started_monotonic_ns=start,
                bytes=len(raw),
                lane="control" if urgent else "audio",
            )

    async def model_message(self, row):
        kind = row["type"]
        if kind == "audio":
            sid = row["synthesis_id"]
            raw = base64.b64decode(row["pcm_f32le"], validate=True)
            if row["sample_rate"] != 24000 or len(raw) % 4:
                raise ValueError("native playback requires mono float32 at 24k")
            count = len(raw) // 4
            start = row["end_sample"] - count
            for offset in range(0, count, 7680):
                # Cancellation can race a pipe write; never revive its generation.
                if sid in self.cancelled:
                    break
                data = raw[offset * 4 : (offset + 7680) * 4]
                await self.command(
                    {
                        "type": "audio",
                        "synthesis_id": sid,
                        "pcm_f32le": base64.b64encode(data).decode(),
                        "end_sample": start + offset + len(data) // 4,
                    }
                )
                self.sent_audio.add(sid)
            return  # PCM never travels through the browser in native mode.
        if kind == "interrupt":
            sid = row["synthesis_id"]
            self.cancelled.add(sid)
            await self.command({"type": "cancel", "synthesis_id": sid})
        elif kind == "synthesis_done":
            sid = row["synthesis_id"]
            if (
                row["completed"]
                and sid in self.sent_audio
                and sid not in self.cancelled
            ):
                await self.command({"type": "synthesis_done", "synthesis_id": sid})
        await self.send(row)

    async def control_blocks(self):
        loop = asyncio.get_running_loop()
        for block in self.pcm.fast_blocks():
            started = time.monotonic_ns()
            probability = await loop.run_in_executor(self.control, self.vad.feed, block)
            self.probabilities.append(probability)
            self.record(
                "fast_vad",
                start_sample=self.fast_position,
                end_sample=self.fast_position + 512,
                probability=probability,
                execution_ns=time.monotonic_ns() - started,
            )
            self.fast_position += 512
            if probability >= 0.5:
                self.session.note_speech_observed(self.fast_position)
                await self.session.interrupt("native_vad_speech")
        if len(self.probabilities) > 64:
            raise ValueError("reference alignment delayed more than 2.048s")

    async def admit(self, blocks):
        for microphone, reference in blocks:
            if not self.probabilities:
                raise ValueError("native model block has no matching VAD block")
            self.session.admit(
                np.concatenate((microphone, reference)).astype("<f4").tobytes(),
                vad_probability=self.probabilities.popleft(),
            )
            self.admitted_position += 512
            self.last_admitted_at = time.monotonic()

    async def end(self):
        async with self.input_lock:
            await self.end_locked()

    async def end_locked(self):
        if not self.capture_ready:
            raise ValueError("native input has not become ready")
        if not self.ending:
            self.ending = True
            self.end_requested_at = time.monotonic()
            self.input_cutoff = self.pcm.positions["microphone"]
            self.record("input_cutoff", native_microphone_samples=self.input_cutoff)
            await self.send(
                {"type": "input_closed", "native_microphone_samples": self.input_cutoff}
            )
        await self.try_finish_input()

    async def try_finish_input(self):
        if self.ending and not self.model_input_done and self.pcm.can_finish():
            blocks, metadata = self.pcm.finish()
            await self.control_blocks()
            await self.admit(blocks)
            if self.probabilities or self.fast_position != self.admitted_position:
                raise ValueError("native VAD/model input counts differ")
            self.evidence.meta["native_input"] = dict(
                metadata,
                admitted_16k_samples=self.admitted_position,
                cutoff_native_sample=self.input_cutoff,
            )
            self.model_input_done = True
            await self.session.finish_input()

    async def native_event(self, row):
        kind = row.get("type")
        if kind == "ready":
            if self.pcm is not None:
                raise ValueError("duplicate native readiness")
            if (
                row["input"]["uid"] != self.input_uid
                or row["output"]["uid"] != self.output_uid
            ):
                raise ValueError("native host selected different devices")
            self.pcm = NativePCM(row)
            self.evidence.meta["native_ready"] = row
            # Native `ready` only attests graph setup. User-visible readiness
            # requires both timestamped PCM streams, VAD and model admission.
        elif kind == "audio":
            async with self.input_lock:
                if self.pcm is None:
                    raise ValueError("audio preceded native readiness")
                if not self.model_input_done and not (
                    self.ending and row["stream"] == "microphone"
                ):
                    blocks = self.pcm.push(row)
                    await self.control_blocks()
                    await self.admit(blocks)
                    if not self.capture_ready and self.admitted_position:
                        self.capture_ready = True
                        self.ready_at = time.monotonic()
                        capture = {
                            "native_samples": dict(self.pcm.positions),
                            "admitted_16k_samples": self.admitted_position,
                            "native_audio_sequence": row["sequence"],
                        }
                        self.evidence.meta["native_capture_ready"] = capture
                        self.record("capture_ready", **capture)
                        await self.send(
                            {
                                "type": "ready",
                                "session_id": self.evidence.id,
                                "native": self.evidence.meta["native_ready"],
                                "capture": capture,
                            }
                        )
                    await self.try_finish_input()
        elif kind in {"playback_start", "played", "playback_stop"}:
            if kind == "playback_start":
                self.native_active.add(row["synthesis_id"])
            elif kind == "playback_stop":
                self.native_active.discard(row["synthesis_id"])
            self.record("native_playback", native=row)
            await self.session.playback(row)
            await self.send({"type": "native_playback", "native": row})
        elif kind == "complete":
            self.native_report = row
            self.evidence.meta["native_terminal"] = {
                k: v
                for k, v in row.items()
                if k not in {"sequence", "observed_host_ticks"}
            }
            if (
                row["status"] != "passed"
                or row["reason"] != "finished"
                or not self.finishing_host
            ):
                raise RuntimeError(
                    f"native host ended unexpectedly: {row.get('error', row['reason'])}"
                )
            if (
                row["capture_drops"]
                or row["render_drops"]
                or not row["private_aggregate_removed"]
                or row["defaults_before"] != row["defaults_after"]
            ):
                raise RuntimeError("native cleanup/audio continuity failed")
        elif kind == "error":
            raise RuntimeError(f"native host failure: {row.get('reason')}")

    async def read_events(self, path):
        pending = b""
        started = time.monotonic()
        last = started
        with path.open("rb") as source:
            while True:
                self.check_progress(time.monotonic(), started, last)
                await self.maybe_end_for_duration(time.monotonic())
                if time.monotonic() - self.last_resource_sample >= 5:
                    sample = self.record_resources()
                    await self.send({**sample, "type": "progress"})
                data = source.read(131072)
                if data:
                    pending += data
                    last = time.monotonic()
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        if line.startswith(b"{"):
                            await self.native_event(json.loads(line))
                    if len(pending) > 131072:
                        raise ValueError("native event line exceeded bound")
                elif self.native_report is not None:
                    if pending:
                        raise ValueError("partial terminal native event")
                    return
                else:
                    if self.process.returncode is not None:
                        # LaunchServices completion without a native terminal is not success.
                        raise RuntimeError(
                            "native launcher exited without a complete report"
                        )
                    await asyncio.sleep(0.005)

    async def maybe_end_for_duration(self, now):
        if (
            self.capture_ready
            and not self.ending
            and now - self.ready_at >= self.input_limit_seconds
        ):
            self.record("input_duration_limit", seconds=self.input_limit_seconds)
            await self.end()

    def record_resources(self):
        # This bridge is macOS-only. Peak RSS is a high-water mark, not current RSS.
        import resource

        self.last_resource_sample = time.monotonic()
        sample = self.record(
            "resource_sample",
            process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            input_queue_chunks=self.session.audio_queue.qsize(),
            pending_vad_blocks=len(self.probabilities),
            admitted_16k_samples=self.admitted_position,
            open_audio_files=sum(not f.closed for f in self.evidence.files.values()),
            evidence_free_bytes=shutil.disk_usage(self.evidence.path).free,
            temporary_free_bytes=shutil.disk_usage(tempfile.gettempdir()).free,
        )
        if (
            shutil.disk_usage(self.evidence.path).free < 128 * 1024 * 1024
            or shutil.disk_usage(tempfile.gettempdir()).free < 128 * 1024 * 1024
        ):
            raise RuntimeError("native evidence disk reached safety floor")
        return sample

    def check_progress(self, now, started, last_event):
        if self.native_report is not None:
            return
        if not self.capture_ready and now - started > 20:
            raise TimeoutError("native startup did not supply validated paired audio")
        if (
            self.capture_ready
            and not self.model_input_done
            and now - self.last_admitted_at > 3
        ):
            raise TimeoutError("native model audio admission stalled")
        if now - last_event > (20 if self.pcm is None else 3):
            raise TimeoutError("native audio event stream stalled")
        if (
            self.ending
            and not self.model_input_done
            and now - self.end_requested_at > 1
        ):
            raise TimeoutError("native reference did not cover the microphone cutoff")

    async def run(self):
        from scripts.probe_macos_audio_host import APP, identity

        self.vad = await asyncio.get_running_loop().run_in_executor(
            self.control, ControlVAD, self.root
        )
        self.evidence.meta["control_vad"] = self.vad.identity
        self.evidence.meta["native_binding"] = self.binding
        # Includes duplicated native JSON/base64 evidence, model PCM, and headroom;
        # this is a preflight, not a filesystem reservation or a silent deletion policy.
        evidence_needed = self.host_limit_seconds * 3 * 1024 * 1024 + 512 * 1024 * 1024
        temporary_needed = self.host_limit_seconds * 1024 * 1024 + 128 * 1024 * 1024
        if (
            shutil.disk_usage(self.evidence.path).free < evidence_needed
            or shutil.disk_usage(tempfile.gettempdir()).free < temporary_needed
        ):
            raise RuntimeError("insufficient disk headroom for bounded native evidence")
        self.evidence.meta["session_bounds"] = {
            "input_seconds": self.input_limit_seconds,
            "host_seconds": self.host_limit_seconds,
            "evidence_headroom_bytes": evidence_needed,
            "temporary_headroom_bytes": temporary_needed,
        }
        self.temporary = tempfile.TemporaryDirectory(prefix="aii-native-loop-")
        temp = Path(self.temporary.name)
        fifo = temp / "input.fifo"
        os.mkfifo(fifo, 0o600)
        self.fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
        control_fifo = temp / "control.fifo"
        os.mkfifo(control_fifo, 0o600)
        self.control_fd = os.open(control_fifo, os.O_RDWR | os.O_NONBLOCK)
        stdout, stderr = temp / "stdout.jsonl", temp / "stderr.log"
        stdout.touch(mode=0o600)
        stderr.touch(mode=0o600)
        with tarfile.open(self.evidence.path / "source.tar.gz", "w:gz") as archive:
            sources = {
                **self.evidence.meta["identity"]["source_files"],
                **self.binding["source_files"],
            }
            for name, expected in sources.items():
                path = self.root / name
                if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    raise ValueError("bridge source changed since server start")
                archive.add(path, arcname=name, recursive=False)
        self.process = await asyncio.create_subprocess_exec(
            "open",
            "-n",
            "-W",
            "--stdin",
            str(fifo),
            "--stdout",
            str(stdout),
            "--stderr",
            str(stderr),
            str(APP),
            "--args",
            "run",
            "--input-uid",
            self.input_uid,
            "--output-uid",
            self.output_uid,
            "--output",
            str(self.evidence.path / "native-capture"),
            "--duration",
            str(self.host_limit_seconds),
            "--voice-processing",
            "on",
            "--gain",
            "0.6",
            "--stdio",
            "on",
            "--emit-audio",
            "on",
            "--control-fifo",
            str(control_fifo),
        )
        self.reader = asyncio.create_task(self.read_events(stdout))
        self.worker = asyncio.create_task(self.session.run())
        done, _ = await asyncio.wait(
            {self.worker, self.reader}, return_when=asyncio.FIRST_COMPLETED
        )
        if self.reader in done:
            await self.reader
            raise RuntimeError("native input ended before the model session")
        await self.worker
        self.finishing_host = True
        await self.command({"type": "finish"})
        await asyncio.wait_for(asyncio.shield(self.reader), 20)
        await asyncio.wait_for(self.process.wait(), 10)
        if self.native_active or identity() != self.binding:
            raise RuntimeError("native playback/binding did not close exactly")
        self.evidence.meta["native_binding_unchanged"] = True

    async def close(self):
        if self.closed:
            return
        self.closed = True
        self.session.abort()
        if self.fd is not None and self.native_report is None:
            try:
                await self.command({"type": "stop"})
            except (OSError, RuntimeError, TimeoutError) as error:
                self.record("cleanup_error", reason=str(error))
        if self.worker and not self.worker.done():
            self.worker.cancel()
        if self.worker:
            await asyncio.gather(self.worker, return_exceptions=True)
        if self.session.synthesis_task:
            await asyncio.gather(self.session.synthesis_task, return_exceptions=True)
        if self.process:
            try:
                await asyncio.wait_for(self.process.wait(), 10)
            except TimeoutError:
                self.record(
                    "cleanup_error",
                    reason=f"native exit not proven; {self.host_limit_seconds}s host bound remains",
                )
        if self.reader and not self.reader.done():
            self.reader.cancel()
        if self.reader:
            await asyncio.gather(self.reader, return_exceptions=True)
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.control_fd is not None:
            os.close(self.control_fd)
            self.control_fd = None
        try:
            await finalize_off_loop(self.save_artifacts)
        finally:
            self.control.shutdown(wait=True, cancel_futures=True)

    def save_artifacts(self):
        if self.temporary:
            temp = Path(self.temporary.name)
            for name in ("stdout.jsonl", "stderr.log"):
                if (temp / name).exists():
                    shutil.copyfile(temp / name, self.evidence.path / f"native-{name}")
            # Preserve temporary files if native exit is not actually proven.
            if self.process is None or self.process.returncode is not None:
                self.temporary.cleanup()
            else:
                self.temporary._finalizer.detach()
                self.evidence.meta["native_temporary_retained"] = str(temp)
        self.journal.close()
        self.journal.export(self.evidence.path / "bridge.json")
        self.evidence.meta["native_artifacts"] = {
            str(p.relative_to(self.evidence.path)): file_identity(p)
            for p in self.evidence.path.rglob("*")
            if p.is_file()
            and (
                p.relative_to(self.evidence.path).parts[0] == "native-capture"
                or p.name
                in {
                    "native-stdout.jsonl",
                    "native-stderr.log",
                    "source.tar.gz",
                    "bridge.json",
                    "bridge-events.jsonl",
                }
            )
        }
