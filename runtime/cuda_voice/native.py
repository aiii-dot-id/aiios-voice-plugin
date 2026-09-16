"""Explicit PulseAudio endpoints behind the shared native session contract.

The Pulse client has its own realtime thread. Python inference never holds its
control lock. Playback credits mean server-accepted samples, not acoustic proof.
An external, explicitly bound echo-cancellation endpoint supplies near-end PCM.
"""

import asyncio
import base64
import hashlib
import json
import subprocess
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import numpy as np

from runtime.voice_core.control_vad import ControlVAD
from runtime.voice_core.evidence_io import EventSpool
from runtime.voice_core.pulse_audio import PulseAudio

from .session import CUDASession


def binding(root, config):
    return {
        "backend": "pulseaudio",
        "library_sha256": hashlib.sha256(
            (root / "libvf_pulse.so").read_bytes()
        ).hexdigest(),
        "source": config["source"],
        "sink": config["sink"],
        "echo_frontend": config["echo_frontend"],
        "credit_scope": "server_written_not_acoustic",
    }


async def inventory(config):
    devices = []
    for plural, selected in (("sources", config["source"]), ("sinks", config["sink"])):
        result = await asyncio.to_thread(
            subprocess.run,
            ["pactl", "--format=json", "list", plural],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        matches = [r for r in json.loads(result.stdout) if r["name"] == selected]
        if len(matches) != 1:
            raise RuntimeError("explicit native audio endpoint unavailable")
        devices.append(
            {
                "uid": selected,
                "name": matches[0].get("description", selected),
                "input_channels": int(plural == "sources"),
                "output_channels": int(plural == "sinks"),
            }
        )
    return {"devices": devices, "defaults_changed": False}


class NativeBridge:
    def __init__(
        self,
        root,
        expected,
        models,
        executor,
        send,
        evidence,
        reply,
        input_uid,
        output_uid,
        *,
        binding_validator=binding,
        host_factory=None,
    ):
        if binding_validator(root, expected) != expected:
            raise ValueError("native library binding changed")
        if (input_uid, output_uid) != (expected["source"], expected["sink"]):
            raise ValueError("native device selection differs from explicit binding")
        self.root, self.binding, self.send, self.evidence = (
            root,
            expected,
            send,
            evidence,
        )
        self.open_host = host_factory or partial(
            PulseAudio,
            root / "libvf_pulse.so",
            sink=expected["sink"],
            source=expected["source"],
        )
        self.session = CUDASession(
            models, executor, self.model_message, evidence, reply, precomputed_vad=True
        )
        self.control = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pulse-vad")
        self.host = self.vad = None
        self.worker = self.capture_task = self.output_task = None
        self.capture_done = asyncio.Event()
        self.closing = self.closed = False
        self.cutoff = None
        self.position = 0
        self.queue = deque()
        self.queued_samples = 0
        self.cancelled = set()
        self.active = None
        self.next_epoch = 0
        self.journal = EventSpool(evidence.path / "bridge-events.jsonl")

    def record(self, kind, **fields):
        self.journal.append(dict(type=kind, at_ns=time.monotonic_ns(), **fields))

    async def end(self):
        if self.host is None:
            raise ValueError("native capture has not opened")
        if self.cutoff is None:
            self.cutoff = self.host.finish_input()  # Admission only, no inference join.
            self.record("input_cutoff", samples=self.cutoff)
            await self.send(
                {"type": "input_closed", "native_microphone_samples": self.cutoff}
            )

    async def model_message(self, row):
        kind, sid = row["type"], row.get("synthesis_id")
        if kind == "audio":
            if sid in self.cancelled:
                return
            raw = base64.b64decode(row["pcm_f32le"], validate=True)
            if (
                row["sample_rate"] != 24000
                or len(raw) % 4
                or hashlib.sha256(raw).hexdigest() != row["event"]["content_sha256"]
            ):
                raise ValueError("native PCM format or hash differs")
            pcm = np.frombuffer(raw, dtype="<f4").copy()
            if (
                not np.isfinite(pcm).all()
                or self.queued_samples + len(pcm) > 72000
                or len(self.queue) >= 128
            ):
                raise ValueError("native output invalid or exceeded bounded admission")
            for start in range(0, len(pcm), 2400):
                self.queue.append((sid, pcm[start : start + 2400]))
            self.queued_samples += len(pcm)
            return  # No native PCM is sent to a browser.
        if kind == "interrupt":
            self.cancelled.add(sid)
            self.queue = deque((s, p) for s, p in self.queue if s != sid)
            self.queued_samples = sum(len(p) for _, p in self.queue if p is not None)
            if self.active and self.active["sid"] == sid:
                state = self.host.status()
                if state["state"] in (1, 2):
                    start = time.monotonic_ns()
                    self.host.stop(self.active["epoch"])
                    self.record(
                        "stop_admitted", sid=sid, elapsed_ns=time.monotonic_ns() - start
                    )
        elif (
            kind == "synthesis_done" and row["completed"] and sid not in self.cancelled
        ):
            self.queue.append((sid, None))
        await self.send(row)

    async def playback_event(self, kind, **fields):
        row = dict(type=kind, **fields)
        await self.session.playback(row)
        self.record("playback", event=row)
        await self.send({"type": "native_playback", "native": row})

    async def playback(self):
        while not self.closing:
            state = self.host.status()
            if self.active:
                sid = self.active["sid"]
                if state["epoch"] != self.active["epoch"]:
                    raise RuntimeError("native playback epoch changed")
                if state["written"] > self.active["acked"]:
                    await self.playback_event(
                        "played", synthesis_id=sid, samples=state["written"]
                    )
                    self.active["acked"] = state["written"]
                if state["state"] == 0:
                    if sid not in self.cancelled and (
                        not self.active["ended"]
                        or state["submitted"] != state["written"]
                        or state["written"] != self.session.output_lengths[sid]
                    ):
                        raise RuntimeError("native drain did not cover exact output")
                    self.record(
                        "output_terminal",
                        sid=sid,
                        status=state,
                        cancelled=sid in self.cancelled,
                    )
                    await self.playback_event("playback_stop", synthesis_id=sid)
                    self.active = None
            if self.queue:
                sid, pcm = self.queue[0]
                if sid in self.cancelled:
                    self.queue.popleft()
                    self.queued_samples -= len(pcm) if pcm is not None else 0
                    continue
                if self.active is None:
                    if pcm is None:
                        raise RuntimeError("native end without audio")
                    self.next_epoch += 1
                    self.host.begin(self.next_epoch)
                    self.active = {
                        "sid": sid,
                        "epoch": self.next_epoch,
                        "acked": 0,
                        "ended": False,
                    }
                    await self.playback_event("playback_start", synthesis_id=sid)
                    state = self.host.status()
                if self.active["sid"] == sid and state["state"] == 1:
                    if pcm is None:
                        self.host.end(self.active["epoch"])
                        self.active["ended"] = True
                        self.queue.popleft()
                    elif state["output_available"] >= len(pcm):
                        self.host.write(self.active["epoch"], pcm)
                        self.queue.popleft()
                        self.queued_samples -= len(pcm)
            await asyncio.sleep(0.005)

    async def admit(self, block):
        probability = float(
            await asyncio.get_running_loop().run_in_executor(
                self.control, self.vad.feed, block
            )
        )
        self.position += 512
        if probability >= 0.5:
            self.session.note_speech_observed(self.position)
            await self.session.interrupt("native_vad_speech")
        self.session.admit(
            np.concatenate((block, np.zeros(512))).astype("<f4").tobytes(),
            vad_probability=probability,
        )

    async def capture(self):
        pending = np.empty(0, np.float32)
        ready = False
        started = last = time.monotonic()
        limit = self.evidence.meta["settings"]["max_session_seconds"]
        while not self.closing:
            if self.cutoff is None and time.monotonic() - started >= limit:
                await self.end()
            values = self.host.read()
            if len(values):
                last = time.monotonic()
                pending = np.concatenate((pending, values))
                if len(pending) >= 512:
                    await self.admit(pending[:512])
                    pending = pending[512:]
                    if not ready:
                        ready = True
                        await self.send(
                            {
                                "type": "ready",
                                "session_id": self.evidence.id,
                                "native": self.binding,
                                "capture": {"admitted_16k_samples": self.position},
                            }
                        )
            state = self.host.status()
            if state["capture_holes"]:
                raise RuntimeError("native capture contained holes")
            if (
                self.cutoff is not None
                and state["read"] == self.cutoff
                and state["input_finished"]
            ):
                padding = (-len(pending)) % 512
                if len(pending):
                    await self.admit(np.pad(pending, (0, padding)))
                if self.position - padding != self.cutoff:
                    raise RuntimeError("native input tail counts disagree")
                self.evidence.meta["native_input"] = {
                    "cutoff_16k_samples": self.cutoff,
                    "padding_samples": padding,
                    "admitted_16k_samples": self.position,
                    "status": state,
                }
                await self.session.finish_input()
                self.capture_done.set()
                return
            if time.monotonic() - last > 3:
                raise RuntimeError("native capture or input acknowledgement stalled")
            await asyncio.sleep(0.002)

    async def run(self):
        loop = asyncio.get_running_loop()
        self.vad = await loop.run_in_executor(self.control, ControlVAD, self.root)
        # Retain ownership if opening is cancelled while the native thread starts.
        opening = asyncio.create_task(asyncio.to_thread(self.open_host))
        try:
            self.host = await asyncio.shield(opening)
        except asyncio.CancelledError:
            self.host = await opening
            raise
        self.evidence.meta["native_binding"] = self.binding
        self.evidence.meta["control_vad"] = self.vad.identity
        self.worker = asyncio.create_task(self.session.run())
        self.capture_task = asyncio.create_task(self.capture())
        self.output_task = asyncio.create_task(self.playback())
        done, _ = await asyncio.wait(
            {self.worker, self.capture_task, self.output_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if self.capture_task in done:
            await self.capture_task
            done, _ = await asyncio.wait(
                {self.worker, self.output_task}, return_when=asyncio.FIRST_COMPLETED
            )
        if self.output_task in done:
            await self.output_task
            raise RuntimeError("native output worker ended early")
        await self.worker
        if not self.capture_done.is_set() or self.active or self.queue:
            raise RuntimeError("native session has unresolved input/output")
        self.evidence.meta["native_terminal"] = self.host.status()

    async def close(self):
        if self.closed:
            return
        self.closed = self.closing = True
        try:
            self.session.abort()
            if self.host:
                state = self.host.status()
                if state["state"] in (1, 2):
                    self.host.stop(state["epoch"])
        finally:
            tasks = [t for t in (self.worker, self.capture_task, self.output_task) if t]
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                self.control.shutdown(wait=True)
                if self.host:
                    await asyncio.to_thread(self.host.close)
            finally:
                self.journal.close()
