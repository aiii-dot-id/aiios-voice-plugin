"""Bounded ASR/VAD/TTS session shared by browser and recorded-input clients.

Optional semantic pause guard is development-scoped, not a qualified controller.
Browser playback acknowledgements are client reports, not DAC measurements.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from runtime.speech_output import SpeechOutput

from .application import ApplicationReplies
from .evidence_io import EventSpool, file_identity
from .protocol import validate_event_stream

RATE = 16000
CHUNK = 512
PACKET_BYTES = CHUNK * 2 * 4


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def decode_packet(packet):
    if len(packet) != PACKET_BYTES:
        raise ValueError(f"audio packet must be {PACKET_BYTES} bytes")
    values = np.frombuffer(packet, dtype="<f4")
    if not np.isfinite(values).all() or np.max(np.abs(values)) > 1.001:
        raise ValueError("audio packet must be finite normalized float32")
    return values[:CHUNK].copy(), values[CHUNK:].copy()


class Evidence:
    def __init__(self, root, identity, settings, input_kind):
        self.id = uuid.uuid4().hex
        # LaunchServices does not inherit the shell's working directory.
        self.path = Path(root).resolve() / self.id
        self.path.mkdir(parents=True, mode=0o700)
        self.files = {}
        self.endpoint_decisions = EventSpool(self.path / "endpoint-decisions.jsonl")
        self.ended = False
        self.trace = {
            "schema": "aiii.voice.core.trace",
            "schema_version": 1,
            "session_id": self.id,
            "system": {
                "system_manifest_sha256": hashlib.sha256(
                    canonical(identity)
                ).hexdigest(),
                "runtime": {
                    "name": "aii-voice-live-reference",
                    "revision": identity["source_sha256"],
                    "backend": identity.get("backend", "mlx-metal"),
                    "precision": "manifest-bound-mixed",
                    "settings_sha256": hashlib.sha256(canonical(settings)).hexdigest(),
                },
            },
            "audio_streams": [
                {
                    "stream_id": name,
                    "direction": "input",
                    "sample_rate_hz": RATE,
                    "channels": 1,
                    "sample_type": "pcm_f32le",
                }
                for name in ("microphone", "playback-reference", "near-end")
            ]
            + [
                {
                    "stream_id": "synthesis",
                    "direction": "output",
                    "sample_rate_hz": 24000,
                    "channels": 1,
                    "sample_type": "pcm_f32le",
                }
            ],
            # Audio callbacks run on another clock: do not invent clock synchronization.
            "timing": {"mode": "replay", "arrival_rate": 1.0, "latency_claims": False},
            "events": EventSpool(self.path / "events.jsonl"),
        }
        self.meta = {
            "input_kind": input_kind,
            "identity": identity,
            "settings": settings,
            "clock_qualification": "server observation diagnostics only; not synchronized DAC latency",
            "endpoint_policy": settings.get(
                "endpoint_policy",
                "Silero acoustic threshold and 640ms silence; not semantic",
            ),
            "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self.emit("session_start")
        self.emit("state_reset", scope="session")

    def emit(self, event_type, **fields):
        if self.ended:
            raise RuntimeError("cannot emit after terminal")
        sequence = len(self.trace["events"]) + 1
        event = {
            "sequence": sequence,
            "event_id": f"e{sequence}",
            "type": event_type,
            "observed_monotonic_ns": time.monotonic_ns(),
            **fields,
        }
        self.trace["events"].append(event)
        return event

    def audio(self, kind, samples, start, *, synthesis_id=None, stream_id=None):
        payload = np.asarray(samples, dtype="<f4").tobytes()
        stream = (
            "synthesis"
            if kind == "audio_chunk"
            else ("microphone" if kind == "input_audio" else "playback-reference")
        )
        if stream_id is not None:
            stream = stream_id
        name = f"{synthesis_id or stream}.f32le"
        if name not in self.files:
            self.files[name] = (self.path / name).open("xb")
        self.files[name].write(payload)
        fields = {
            "stream_id": stream,
            "start_sample": start,
            "end_sample": start + len(samples),
            "content_sha256": hashlib.sha256(payload).hexdigest(),
        }
        if synthesis_id:
            fields["synthesis_id"] = synthesis_id
        return self.emit(kind, **fields)

    def finish(self, kind="session_end", reason=""):
        if self.ended:
            return
        self.emit(
            kind,
            **(
                {"status": "completed"} if kind == "session_end" else {"reason": reason}
            ),
        )
        self.ended = True
        for handle in self.files.values():
            handle.close()
        self.endpoint_decisions.close()
        self.meta["endpoint_decisions"] = file_identity(self.endpoint_decisions.path)
        events = self.trace["events"]
        events.close()
        metadata = {k: v for k, v in self.trace.items() if k != "events"}
        validation_error = None
        try:
            summary = validate_event_stream(metadata, events)
            self.meta["validation"] = summary
        except ValueError as error:
            # A broken trace is evidence of a defect, not permission to erase it.
            validation_error = error
            self.meta["validation_error"] = str(error)
        self.meta["audio_files"] = {
            name: file_identity(self.path / name) for name in self.files
        }
        events.export(self.path / "trace.json", metadata)
        self.meta["event_journal"] = file_identity(events.path)
        (self.path / "report.json").write_text(
            json.dumps(self.meta, indent=2, sort_keys=True) + "\n"
        )
        if validation_error is not None:
            raise validation_error
        return summary

    def close_audio(self, synthesis_id):
        handle = self.files.get(f"{synthesis_id}.f32le")
        if handle:
            handle.close()


class LiveSession:
    def __init__(
        self,
        models,
        executor,
        send,
        evidence,
        reply,
        echo=None,
        *,
        precomputed_vad=False,
    ):
        self.models, self.executor, self.send, self.evidence = (
            models,
            executor,
            send,
            evidence,
        )
        self.reply = reply
        self.audio_queue = asyncio.Queue(maxsize=64)
        self.input_ready = asyncio.Event()
        self.admitted_samples = 0
        self.control_executor = self.control_vad = None
        self.position = 0
        self.received_position = 0
        self.echo = echo
        self.precomputed_vad = precomputed_vad
        if precomputed_vad and echo is not None:
            raise ValueError("precomputed VAD requires already processed input")
        self.capture_stream = "near-end" if echo is not None else "microphone"
        self.cancel_reason = "interrupted"
        # Keep opening speech during AEC convergence and VAD lookback. This is
        # retained input, not a one-second delay before admitting a turn.
        default_preroll = 32 if echo is not None else 8
        preroll_frames = getattr(models, "stt_preroll_frames", default_preroll)
        if (
            type(preroll_frames) is not int
            or not default_preroll <= preroll_frames <= 32
        ):
            raise ValueError(
                "Recognition preroll must preserve the default within 32 frames"
            )
        self.preroll = deque(maxlen=preroll_frames)
        self.evidence.meta["recognition_preroll_frames"] = preroll_frames
        self.vad_state = None
        self.stream = None
        self.speech_start = 0
        self.silence = 0
        self.acoustic_pause_samples = 10240
        self.turn_number = 0
        self.revision = 0
        self.last_text = ""
        self.synthesis_task = None
        self.output = SpeechOutput(
            SimpleNamespace(
                max_tokens=256,
                tts_stream=lambda text: self.models.tts_stream(text),
                tts_next=lambda generator: self.models.tts_next(generator),
            ),
            executor,
        )
        self.output_job = None
        self.application = None
        self.response_tasks = set()
        self.response_error = None
        self.response_timeout = 30.0
        self.cancel_generation = False
        self.active_synthesis = None
        self.generated = 0
        self.acknowledged = 0
        self.credit = asyncio.Event()
        self.playing = set()
        self.known_synthesis = set()
        self.resolved_synthesis = set()
        self.interrupted = set()
        self.output_lengths = {}
        self.output_acks = {}
        self.input_ended = False
        self.observed_speech_position = 0
        self.endpoint_audio = []
        self.endpoint_boundary = None
        self.endpoint = None
        if getattr(models, "endpoint", None) is not None:
            from .semantic_endpoint import PauseGate

            self.endpoint = PauseGate(
                models.endpoint.probability,
                models.endpoint_executor,
                self.evidence.endpoint_decisions.append,
            )

    def configure_pause(self, milliseconds):
        """Session-open policy only; never retime a partly consumed utterance."""
        from .semantic_endpoint import pause_samples

        if self.position or self.stream is not None:
            raise ValueError("pause settings are pinned before session input")
        samples = pause_samples(milliseconds)
        if self.endpoint is not None:
            self.evidence.meta["endpoint_policy"] = self.endpoint.configure_pause(
                milliseconds
            )
        else:
            self.evidence.meta["endpoint_policy"] = {
                "clock": "ordered_audio_samples",
                "sample_rate": RATE,
                "requested_pause_ms": milliseconds,
                "commitment_samples": samples,
                "semantic": False,
            }
        self.acoustic_pause_samples = samples

    def note_speech_observed(self, position):
        # The independent native control lane can be ahead of the MLX lane.
        self.observed_speech_position = max(self.observed_speech_position, position)

    def enable_application(self, *, timeout=30.0):
        if self.application or self.turn_number or not 0.1 <= timeout <= 60:
            raise ValueError("application mode must be configured once before input")
        self.application = ApplicationReplies(self.evidence.id)
        self.response_timeout = timeout

    def submit_reply(self, session_id, turn_id, request_id, text):
        if not self.application:
            raise ValueError("session does not use application replies")
        return self.application.submit(session_id, turn_id, request_id, text)

    def status(self):
        request = self.application.current if self.application else None
        pending = bool(
            request and self.application.live(request) and not request.accepted
        )
        return {
            "type": "status",
            "session_id": self.evidence.id,
            "input": "closed" if self.input_ended else "open",
            "recognition": "recognizing" if self.stream else "idle",
            "application": "awaiting_reply" if pending else "idle",
            "synthesis": self.output_job.snapshot()
            if self.output_job
            else {"state": "idle"},
            "playback": {
                "state": "playing" if self.playing else "idle",
                "synthesis_ids": sorted(self.playing),
            },
            "draining": bool(
                self.input_ended
                and (
                    pending
                    or self.response_tasks
                    or self.synthesis_task
                    and not self.synthesis_task.done()
                    or self.playing
                )
            ),
            "admitted_samples": self.admitted_samples,
            "processed_samples": self.position,
        }

    def abort(self):
        if self.application:
            self.application.close()
        self.cancel_generation = True
        self.credit.set()
        if self.output_job:
            self.output.cancel(self.output_job)

    def application_request(self, tid):
        if len(self.response_tasks) >= 8:
            raise RuntimeError("application response cleanup exceeded eight tasks")
        request = self.application.request(tid, self.last_text)
        task = asyncio.create_task(self.respond(request, f"s{self.turn_number}"))
        self.response_tasks.add(task)

        def completed(done):
            self.response_tasks.discard(done)
            if not done.cancelled() and done.exception() is not None:
                self.response_error = done.exception()
                self.input_ready.set()

        task.add_done_callback(completed)

    async def respond(self, request, sid):
        # Network/app waiting and model cleanup must never hold the input owner.
        try:
            async with asyncio.timeout(self.response_timeout):
                await self.send(request.message())
                text = await asyncio.shield(request.future)
        except TimeoutError as error:
            raise TimeoutError("application reply deadline exceeded") from error
        if not self.application.live(request) or text is None:
            return
        if self.synthesis_task:
            await asyncio.shield(self.synthesis_task)
        if not self.application.live(request):
            return
        # A completed generator may still have native audio queued. Do not
        # overlap generations at the endpoint, even after component PCM drained.
        async with asyncio.timeout(15):
            while self.playing and self.application.live(request):
                await asyncio.sleep(0.01)
        if self.application.live(request):
            await asyncio.shield(self.begin_synthesis(sid, text))

    async def gpu(self, function, *args, **kwargs):
        return await asyncio.get_running_loop().run_in_executor(
            self.executor, partial(function, *args, **kwargs)
        )

    async def event(self, event_type, **fields):
        event = self.evidence.emit(event_type, **fields)
        await self.send({"type": "event", "event": event})

    async def interrupt(self, reason):
        if self.application:
            self.application.invalidate()
        if self.active_synthesis:
            self.cancel_generation = True
            self.credit.set()
            if self.output_job:
                self.output.cancel(self.output_job)
        # Generation can be finished while samples are still queued at the client.
        targets = self.playing | (
            {self.active_synthesis} if self.active_synthesis else set()
        )
        for sid in sorted(targets - self.interrupted):
            self.interrupted.add(sid)
            self.cancel_reason = reason
            # The server admits notifications to a bounded nonblocking writer.
            # Preserve canonical event order, but still issue native stop if the
            # notification queue refuses admission.
            event = self.evidence.emit(
                "interruption_requested", synthesis_id=sid, reason=reason
            )
            try:
                await self.send({"type": "event", "event": event})
            finally:
                await self.send(
                    {"type": "interrupt", "synthesis_id": sid, "reason": reason}
                )

    def admit(self, packet, *, vad_probability=None):
        if self.input_ended:
            raise ValueError("audio after input end")
        pair = decode_packet(packet)
        if self.precomputed_vad:
            if not isinstance(vad_probability, float) or not 0 <= vad_probability <= 1:
                raise ValueError("native input needs finite precomputed VAD")
            pair = (*pair, vad_probability)
        elif vad_probability is not None:
            raise ValueError("unexpected external VAD")
        try:
            self.audio_queue.put_nowait(pair)
            self.admitted_samples += CHUNK
            self.input_ready.set()
        except asyncio.QueueFull as error:
            raise RuntimeError(
                "input queue exceeded 2.048 seconds; no audio silently dropped"
            ) from error

    async def finish_input(self):
        if not self.input_ended:
            self.input_ended = True
            # Half-close admission never waits behind a full inference queue.
            # The input owner drains every accepted chunk before observing EOF.
            self.input_ready.set()

    async def next_audio(self):
        while True:
            if self.response_error:
                raise self.response_error
            try:
                return self.audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                if self.input_ended:
                    return None
                # No await between observing empty and clearing: producers
                # cannot have their wakeup lost on this single event loop.
                self.input_ready.clear()
                await self.input_ready.wait()

    async def playback(self, message):
        sid = message.get("synthesis_id")
        if sid not in self.known_synthesis:
            raise ValueError("playback names unknown synthesis")
        kind = message["type"]
        if kind == "played":
            count = message.get("samples")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("invalid playback acknowledgement")
            if count < self.output_acks.get(sid, 0) or count > self.output_lengths.get(
                sid, 0
            ):
                raise ValueError("playback acknowledgement outside generated range")
            self.output_acks[sid] = count
            if sid == self.active_synthesis:
                self.acknowledged = count
                self.credit.set()
        elif kind == "playback_start":
            if sid in self.playing:
                raise ValueError("duplicate playback start")
            self.playing.add(sid)
            await self.event(kind, synthesis_id=sid)
        elif kind == "playback_stop":
            if sid not in self.playing:
                raise ValueError("playback stop without start")
            self.playing.remove(sid)
            await self.event(kind, synthesis_id=sid)

    async def partials(self, updates):
        for update in updates:
            text = update.result.text.strip()
            if text and text != self.last_text:
                self.last_text = text
                self.revision += 1
                await self.event(
                    "transcript_partial",
                    utterance_id=f"u{self.turn_number}",
                    text=text,
                    revision=self.revision,
                    stream_id=self.capture_stream,
                    start_sample=self.speech_start,
                    end_sample=self.position,
                )

    async def end_utterance(self):
        if self.stream is None:
            return
        updates = await self.gpu(self.stream.finish)
        await self.partials(updates)
        self.last_text = updates[-1].result.text.strip() if updates else ""
        await self.event(
            "speech_end",
            activity_id=f"a{self.turn_number}",
            stream_id=self.capture_stream,
            start_sample=self.position - 1,
            end_sample=self.position,
        )
        self.stream = None
        self.preroll.clear()
        if self.endpoint:
            self.endpoint.reset()
        if not self.last_text and not self.revision:
            return
        self.revision += 1
        await self.event(
            "transcript_final",
            utterance_id=f"u{self.turn_number}",
            text=self.last_text,
            revision=self.revision,
            stream_id=self.capture_stream,
            start_sample=self.speech_start,
            end_sample=self.position,
        )
        tid = f"t{self.turn_number}"
        if not self.last_text:
            return
        await self.event("turn_proposed", turn_event_id=tid)
        await self.event("turn_committed", turn_event_id=tid)
        if self.application:
            if self.observed_speech_position <= self.position:
                self.application_request(tid)
            return
        if self.synthesis_task:
            await self.interrupt("new_turn")
            await self.synthesis_task
        if self.reply and self.observed_speech_position > self.position:
            # Preserve the canonical turn but do not launch a reply over newer
            # speech already seen by the independent native control lane.
            self.evidence.endpoint_decisions.append(
                {
                    "type": "reply_suppressed",
                    "reason": "newer_observed_speech",
                    "position": self.position,
                    "observed_speech_position": self.observed_speech_position,
                }
            )
        elif self.reply:
            self.begin_synthesis(f"s{self.turn_number}")

    def begin_synthesis(self, sid, text=None):
        """Schedule a reply and make it known BEFORE anything can name it.

        The commit and synthesis_start events are journalled here, synchronously,
        and the id is active from this moment. A barge-in that lands during the
        first notification write, or between scheduling and the task's first
        run, therefore finds the synthesis it interrupts, and its
        interruption_requested follows synthesis_start in the trace. Registered
        inside the task, the id would be unknown to such a barge-in: its
        interruption_requested would come first, and the validator would refuse
        the whole session at its end.
        """
        text = self.reply if text is None else text
        self.active_synthesis = sid
        self.known_synthesis.add(sid)
        self.output_lengths[sid] = self.output_acks[sid] = 0
        self.generated = self.acknowledged = 0
        self.cancel_generation = False
        journalled = [
            self.evidence.emit(
                "application_event",
                kind="response_committed",
                payload_sha256=hashlib.sha256(text.encode()).hexdigest(),
            ),
            self.evidence.emit("synthesis_start", synthesis_id=sid),
        ]
        self.synthesis_task = asyncio.create_task(
            self._synthesize_registered(sid, text, journalled)
        )
        # The first done callback, so it runs before anything awaiting the task.
        self.synthesis_task.add_done_callback(partial(self.retire_synthesis, sid))
        return self.synthesis_task

    def resolve_synthesis(self, sid, kind, **fields):
        """Journal the one terminal event of a synthesis."""
        self.resolved_synthesis.add(sid)
        return self.evidence.emit(kind, synthesis_id=sid, **fields)

    def retire_synthesis(self, sid, task):
        """Resolve a synthesis whose task ended without journalling its terminal.

        synthesis_start is journalled before the task runs. A task cancelled
        before its first step runs none of synthesize(), and one that fails
        before its terminal event skips it; either would leave the trace with an
        unresolved synthesis and the id active. Nothing is journalled once the
        trace has ended.
        """
        if self.active_synthesis == sid:
            self.active_synthesis = None
            self.output_job = None
        if sid in self.resolved_synthesis or self.evidence.ended:
            return
        self.resolve_synthesis(
            sid,
            "synthesis_cancelled",
            reason="synthesis_task_cancelled"
            if task.cancelled()
            else "synthesis_task_failed",
        )

    async def synthesize(self, sid, text=None):
        """Preserve the direct awaitable API on the same registered reply path."""
        await self.begin_synthesis(sid, text)

    async def _synthesize_registered(self, sid, text, journalled):
        job = None
        completed = False
        try:
            for event in journalled:
                await self.send({"type": "event", "event": event})
            if not self.cancel_generation:
                # A barge-in before this point already retired the reply.
                job = self.output.submit(text)
                self.output_job = job
            while not self.cancel_generation:
                # Bound outstanding playback to approximately two seconds.
                while (
                    self.generated - self.acknowledged >= 48000
                    and not self.cancel_generation
                ):
                    self.credit.clear()
                    await asyncio.wait_for(self.credit.wait(), timeout=15)
                if self.cancel_generation:
                    break
                result = await job.read()
                if self.cancel_generation:
                    break
                if result is None:
                    completed = (await job.wait())["state"] == "completed"
                    break
                samples, rate = result.samples, result.sample_rate
                event = self.evidence.audio(
                    "audio_chunk", samples, self.generated, synthesis_id=sid
                )
                self.generated += len(samples)
                self.output_lengths[sid] = self.generated
                await self.send(
                    {
                        "type": "audio",
                        "synthesis_id": sid,
                        "sample_rate": rate,
                        "end_sample": self.generated,
                        "event": event,
                        "pcm_f32le": base64.b64encode(
                            samples.astype("<f4").tobytes()
                        ).decode(),
                    }
                )
            terminal = self.resolve_synthesis(
                sid,
                "synthesis_end" if completed else "synthesis_cancelled",
                **({} if completed else {"reason": self.cancel_reason}),
            )
            await self.send({"type": "event", "event": terminal})
            await self.send(
                {"type": "synthesis_done", "synthesis_id": sid, "completed": completed}
            )
        finally:
            try:
                if job is not None:
                    if not completed:
                        self.output.cancel(job)
                    await job.wait()
            finally:
                self.evidence.close_audio(sid)
                self.active_synthesis = None
                self.output_job = None

    async def run(self):
        try:
            await self._run()
        finally:
            self.abort()
            try:
                if self.response_tasks:
                    await asyncio.gather(*self.response_tasks, return_exceptions=True)
                if self.synthesis_task:
                    await asyncio.gather(self.synthesis_task, return_exceptions=True)
                await self.output.close(abort=True)
            finally:
                try:
                    if self.endpoint:
                        await self.endpoint.close()
                finally:
                    if self.control_executor:
                        self.control_executor.shutdown(wait=True)

    async def _run(self):
        if not self.precomputed_vad:
            factory = getattr(self.models, "control_vad_factory", None)
            if factory:
                self.control_executor = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="voice-input-control"
                )
                self.control_vad = await asyncio.get_running_loop().run_in_executor(
                    self.control_executor, factory
                )
                self.evidence.meta["control_vad"] = self.control_vad.identity
            else:
                self.vad_state = await self.gpu(self.models.vad_state)
        while True:
            pair = await self.next_audio()
            if pair is None:
                break
            if self.received_position + CHUNK > RATE * 1860:
                raise RuntimeError("session exceeded 31-minute bound")
            mic, reference = pair[:2]
            probability = pair[2] if self.precomputed_vad else None
            self.evidence.audio("input_audio", mic, self.received_position)
            self.evidence.audio("playback_reference", reference, self.received_position)
            self.received_position += CHUNK
            blocks = self.echo.push(mic, reference) if self.echo is not None else [mic]
            for near in blocks:
                await self.consume_near(near, probability)
            if self.synthesis_task and self.synthesis_task.done():
                await self.synthesis_task  # Never discard generation errors.
                self.synthesis_task = None
        if self.echo is not None:
            for near in self.echo.finish():
                await self.consume_near(near)
            self.evidence.meta["echo_diagnostics"] = self.echo.diagnostics()
        if self.position != self.received_position:
            raise RuntimeError("near-end sample count differs from capture")
        if self.endpoint_audio:
            await self.partials(
                await self.gpu(
                    self.stream.push_audio, np.concatenate(self.endpoint_audio)
                )
            )
            self.endpoint_audio.clear()
        await self.end_utterance()
        if self.response_tasks:
            await asyncio.gather(*self.response_tasks)
        if self.response_error:
            raise self.response_error
        if self.synthesis_task:
            await self.synthesis_task
        deadline = time.monotonic() + 15
        while self.playing and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        if self.playing:
            raise RuntimeError("client did not acknowledge playback stop")

    async def consume_near(self, near, probability=None):
        if self.echo is not None:
            self.evidence.audio(
                "input_audio", near, self.position, stream_id="near-end"
            )
        self.position += CHUNK
        if probability is None:
            if self.control_vad:
                probability = await asyncio.get_running_loop().run_in_executor(
                    self.control_executor, self.control_vad.feed, near
                )
            else:
                probability, self.vad_state = await self.gpu(
                    self.models.vad_feed, near, self.vad_state
                )
        self.evidence.emit(
            "vad_probability",
            probability=probability,
            stream_id=self.capture_stream,
            start_sample=self.position - CHUNK,
            end_sample=self.position,
        )
        self.preroll.append(near)
        speech = probability >= 0.5
        if self.stream is None and speech:
            await self.interrupt(
                "near_end_speech" if self.echo is not None else "vad_speech"
            )
            self.turn_number += 1
            self.revision = 0
            self.last_text = ""
            self.silence = 0
            self.speech_start = self.position - sum(len(p) for p in self.preroll)
            self.stream = await self.gpu(self.models.stt_stream)
            if self.endpoint:
                self.endpoint.reset()
                self.endpoint.append(np.concatenate(self.preroll))
            await self.event(
                "speech_start",
                activity_id=f"a{self.turn_number}",
                stream_id=self.capture_stream,
                start_sample=self.speech_start,
                end_sample=self.position,
            )
            await self.partials(
                await self.gpu(self.stream.push_audio, np.concatenate(self.preroll))
            )
        elif self.stream is not None:
            if self.endpoint:
                self.endpoint.append(near)
            if self.endpoint_boundary is not None:
                self.endpoint_audio.append(near.copy())
                if len(self.endpoint_audio) * CHUNK > (
                    self.endpoint.commitment_samples - self.endpoint.trigger_samples
                ):
                    raise RuntimeError(
                        "semantic provisional window exceeded four retained chunks"
                    )
            else:
                await self.partials(await self.gpu(self.stream.push_audio, near))
        if self.stream is not None:
            self.silence = 0 if speech else self.silence + CHUNK
            reason = None
            if self.endpoint:
                reason = await self.endpoint.poll(
                    speech=speech,
                    silence=self.silence,
                    position=self.position,
                )
            elif self.silence >= self.acoustic_pause_samples:
                reason = "acoustic_silence"
            if self.endpoint:
                if self.endpoint.pending is not None and self.endpoint_boundary is None:
                    self.endpoint_boundary = self.position
                if self.endpoint_boundary is not None and (
                    speech or (self.endpoint.pending is None and not reason)
                ):
                    if self.endpoint_audio:
                        await self.partials(
                            await self.gpu(
                                self.stream.push_audio,
                                np.concatenate(self.endpoint_audio),
                            )
                        )
                    self.endpoint_audio.clear()
                    self.endpoint_boundary = None
            if self.position - self.speech_start >= RATE * 59:
                reason = "utterance_duration_bound"
            if reason:
                self.evidence.endpoint_decisions.append(
                    {
                        "type": "endpoint",
                        "reason": reason,
                        "position": self.position,
                        "silence_samples": self.silence,
                    }
                )
                retained = self.endpoint_audio
                self.endpoint_audio = []
                self.endpoint_boundary = None
                await self.end_utterance()
                self.preroll.extend(retained)
