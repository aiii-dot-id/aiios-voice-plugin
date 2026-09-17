"""One event-loop owner for SDK admission; model and audio work never run inline.

Reuses LiveSession's recognition/turn path and SpeechOutput's synthesis path.
No device enumeration, conversation LLM, credential access or audio recording.
The proposed playback-report control reuses the internal accounting seam;
host/SDK registration and client validation remain integration requirements.
Pipe delivery never manufactures a browser playback receipt.
"""

import asyncio
import hashlib
import time
from collections import deque
from dataclasses import dataclass
from functools import partial

import numpy as np

from runtime.speech_output import SpeechOutput, split_text
from runtime.voice_core.live import CHUNK, LiveSession

from .audio import DISCONTINUITY, END, PCM, Frame
from .capture import capture_processing

# The bounded input queue admits this many PCM frames. One more slot is reserved
# for the end-of-input sentinel, so a finish can never be refused after its
# cutoff is committed: the queue and the 2.048-second admission guard met at
# exactly 64 frames, and a legal finish at that moment committed the cutoff,
# armed the tail deadline, then refused (review, 2026-09-16).
INPUT_QUEUE_FRAMES = 64


class Refused(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(f"{code}: {message}")


def identifier(value, name):
    if not isinstance(value, str) or not value or len(value) > 256:
        raise Refused("INVALID_ID", name)
    return value


class EphemeralEvidence:
    """Adapt the existing recognizer's diagnostics without retaining raw audio."""

    def __init__(self, sid):
        self.id = sid
        self.meta = {}
        self.endpoint_decisions = deque(maxlen=256)

    def emit(self, kind, **fields):
        return {"type": kind, **fields}

    def audio(self, *args, **kwargs):
        return None

    def close_audio(self, *args):
        pass


class Recognition(LiveSession):
    async def gpu(self, function, *args, **kwargs):
        # CUDA STT already has a separate process/executor. Retain that isolation
        # rather than queueing recognition behind Pocket or Qwen synthesis.
        executor = getattr(self.models, "recognition_executor", self.executor)
        return await asyncio.get_running_loop().run_in_executor(
            executor, partial(function, *args, **kwargs)
        )

    def abort(self):
        super().abort()
        recognizer = getattr(self.models, "recognizer", None)
        if recognizer is not None:
            recognizer.cancel()

    async def run(self):
        try:
            await super().run()
        finally:
            recognizer = getattr(self.models, "recognizer", None)
            if recognizer is not None:
                recognizer.cancel()
                await asyncio.get_running_loop().run_in_executor(
                    self.models.recognition_executor, recognizer.retire
                )

    async def interrupt(self, reason):
        # The input control owner has already fenced output before ASR work.
        return None


@dataclass
class Generation:
    id: str
    stream: int
    job: object
    task: object = None
    seq: int = 0
    delivered: int = 0
    fenced: bool = False
    cancelled: bool = False
    terminal: bool = False
    reported: int = 0
    playback_resolved: bool = False


class ResidentEngine:
    """Transport supplies nonblocking emit and bounded asynchronous write_audio."""

    def __init__(
        self,
        models,
        executor,
        control_executor,
        emit,
        write_audio,
        *,
        tail_timeout=3.0,
        drain_timeout=15.0,
        retire_audio=None,
        speaker_tools=None,
        observe_speaker=None,
        speaker_observations=False,
        settings_loader=None,
    ):
        self.models = models
        self.executor, self.control_executor = executor, control_executor
        self.emit_sink, self.write_audio = emit, write_audio
        self.tail_timeout, self.drain_timeout = tail_timeout, drain_timeout
        self.retire_audio = retire_audio
        # Composition supplies the host's effective values, not model-authored
        # open arguments. Awaiting this read belongs to opening, never admission.
        self.settings_loader = settings_loader
        self.effective_settings = None
        self.capture_processing = None
        if type(speaker_observations) is not bool:
            raise ValueError("speaker observations require explicit boolean enablement")
        if speaker_observations and (
            speaker_tools is None or observe_speaker is not None
        ):
            raise ValueError(
                "speaker observations require one service and one event sink"
            )
        if speaker_observations:
            observe_speaker = self.speaker_event
        if (speaker_tools is None) != (observe_speaker is None):
            raise ValueError(
                "private UID integration requires service and observer together"
            )
        self.speaker_tools, self.observe_speaker = speaker_tools, observe_speaker
        self.speaker_observations = speaker_observations
        self.speaker = None
        self.session_ids, self.synthesis_ids = set(), set()
        self.stream_counter = 0
        self.id = None
        self.lifecycle = "closed"
        self.seq = 0
        self.tasks = set()
        self.failure = None
        self.current = None
        self.output = None
        self.recognition = None
        self.drain_changed = asyncio.Event()
        self.abort_requested = False
        self.pending_end = None

    def emit(self, kind, **fields):
        if self.failure is not None and kind != "failure":
            return  # A terminal failure cannot be followed by successful events.
        self.seq += 1
        sequence = self.seq
        self.emit_sink(
            {
                **fields,
                "type": kind,
                "session_id": self.id,
                "id": f"{self.id}:{sequence}",
                "sequence": sequence,
                "observed_monotonic_ns": time.monotonic_ns(),
            }
        )
        return sequence

    def speaker_event(self, result):
        # The session owner supplies correlation, never a model or an observer.
        # A stale result cannot attach to a reused session or another input.
        if (
            result.get("session_id") != self.id
            or result.get("input_handle") != self.input_handle
            or self.lifecycle not in ("opening", "open", "draining")
        ):
            raise ValueError("speaker observation does not belong to the live input")
        from .speaker_event import host_observation

        self.emit(
            "speaker_observation", **host_observation(result, self.speaker_tools.policy)
        )

    def task(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)

        def finished(t):
            self.tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                self.fail(t.exception())
            # Drain waits for actual task retirement, not just an END frame.
            self.drain_changed.set()
            if not self.tasks and self.pending_end is not None and self.failure is None:
                fields, self.pending_end = self.pending_end, None
                self.lifecycle = "closed"
                try:
                    self.emit("session_end", **fields)
                except Exception as error:  # noqa: BLE001 - publication faults the lane
                    self.fail(error)

        task.add_done_callback(finished)
        return task

    def fail(self, error):
        if self.failure is not None:
            return
        self.failure = str(error) or type(error).__name__
        self.pending_end = None
        # The host treats BOTH a failure event and lifecycle=failed as permission
        # to release the endpoint binding. Publish neither until workers retire.
        self.lifecycle = "draining"
        if self.current:
            self.current.fenced = True
            self.current.cancelled = True
            self.output.cancel(self.current.job)
            cancel = getattr(self.models, "cancel_synthesis", None)
            if cancel is not None:
                cancel()
        self.task(self.retire_failure())

    async def retire_failure(self):
        errors = await self.abort_resources()
        self.lifecycle = "failed"
        self.emit(
            "failure",
            reason=self.failure,
            cleanup_errors=errors,
            scope="engine_resources",
            resources_released=True,
            playback_verified=False,
        )

    def admit(self, operation, args):
        if not isinstance(args, dict):
            raise Refused("INVALID_ARGUMENTS", "object required")
        if operation == "speech.session.open":
            return self.open(args)
        if args.get("session_id") != self.id or self.id is None:
            raise Refused("STALE_SESSION", "control must name current session")
        if operation == "speech.session.status":
            return self.status()
        if operation == "speech.session.synthesize":
            return self.synthesize(args)
        if operation in (
            "speech.session.cancel_synthesis",
            "speech.session.stop_playback",
        ):
            return self.fence(args, cancel=operation.endswith("cancel_synthesis"))
        if operation == "speech.session.finish_input":
            return self.finish(args)
        if operation == "speech.session.playback_report":
            required = {
                "session_id",
                "synthesis_id",
                "output_stream",
                "rendered_samples",
                "terminal",
            }
            if set(args) != required:
                raise Refused("PLAYBACK_REPORT", "exact receipt fields required")
            self.playback_report(
                args["synthesis_id"],
                args["output_stream"],
                args["rendered_samples"],
                session_id=args["session_id"],
                terminal=args["terminal"],
            )
            return {
                "accepted": True,
                "synthesis_id": args["synthesis_id"],
                "output_stream": args["output_stream"],
                "rendered_samples": args["rendered_samples"],
                "terminal": args["terminal"],
            }
        if operation == "speech.session.close":
            return self.close(args)
        raise Refused("UNKNOWN_OPERATION", operation)

    def open(self, args):
        if self.lifecycle != "closed" or self.tasks:
            raise Refused("BUSY", "prior engine resources not released")
        sid = identifier(args.get("session_id"), "session_id")
        if sid in self.session_ids or len(self.session_ids) >= 1024:
            raise Refused("ID_REUSE_OR_LIMIT", "fresh activation required")
        audio = args.get("audio", {})
        if not isinstance(audio, dict) or audio.get("format") != "s16le":
            raise Refused("AUDIO_FORMAT", "explicit s16le input and output required")
        for name in ("input", "output"):
            f = audio.get(name, {})
            if (
                not isinstance(f, dict)
                or type(f.get("rate")) is not int
                or not 8000 <= f["rate"] <= 192000
                or type(f.get("channels")) is not int
                or f["channels"] not in (1, 2)
            ):
                raise Refused("AUDIO_FORMAT", name)
        try:
            processing = capture_processing(audio["input"].get("processing"))
        except ValueError as error:
            raise Refused("CAPTURE_PROCESSING", str(error)) from error
        input_handle = identifier(args.get("input_handle"), "input_handle")
        identifier(args.get("output_handle"), "output_handle")
        self.id, self.seq, self.failure = sid, 0, None
        self.session_ids.add(sid)
        self.input_handle = input_handle
        self.input_stream = self.input_seq = None
        self.received = self.processed = self.padding = 0
        self.cutoff = None
        self.end_seen = False
        self.input_signalled = False
        self.input_done = asyncio.Event()
        self.input_completion = None
        self.drain_changed = asyncio.Event()
        self.abort_requested = False
        self.pending_end = None
        self.input_queue = asyncio.Queue(maxsize=INPUT_QUEUE_FRAMES + 1)
        self.effective_settings = None
        self.capture_processing = processing
        if self.speaker_tools is not None:
            from .speaker import SpeakerSession

            self.speaker = SpeakerSession(
                sid,
                input_handle,
                self.speaker_tools,
                self.observe_speaker,
                self.drain_changed.set,
            )
        self.generations = {}
        self.current = None
        self.lifecycle = "opening"
        self.output = SpeechOutput(self.models, self.executor)
        self.recognition = Recognition(
            self.models,
            self.executor,
            self.recognition_event,
            EphemeralEvidence(sid),
            "",
            precomputed_vad=True,
        )
        self.recognition.preroll = deque(maxlen=32)
        self.recognition.evidence.meta["recognition_preroll_frames"] = 32
        self.emit("session_start")
        self.task(self.start_input())
        return {
            "accepted": True,
            "session_id": sid,
            "state": "opening",
            "audio": {
                "input": {"rate": 16000, "channels": 1},
                "output": {"rate": 24000, "channels": 1},
            },
        }

    async def start_input(self):
        loop = asyncio.get_running_loop()
        binder = getattr(self.models, "bind_operator_settings", None)
        if self.settings_loader is not None and binder is None:
            raise RuntimeError("selected backend has no operator settings binding")
        if binder is not None:
            opening_id = self.id
            if self.settings_loader is None:
                values = {}
            else:
                async with asyncio.timeout(2):
                    values = await self.settings_loader(opening_id)
            if (
                self.id != opening_id
                or self.abort_requested
                or self.failure is not None
            ):
                return
            bound = binder(values)
            # Neither owner has started model work; input admitted while opening
            # remains in its original bounded queue. No active generation changes.
            self.recognition.models = bound
            pause_ms = getattr(bound, "turn_pause_ms", None)
            if pause_ms is not None:
                self.recognition.configure_pause(pause_ms)
            self.output.backend = bound
            self.effective_settings = dict(bound.operator_settings)
        factory = getattr(self.models, "control_vad_factory", None)
        if factory is None:
            raise RuntimeError("independent control VAD is required")
        vad = await loop.run_in_executor(self.control_executor, factory)
        self.lifecycle = "open" if self.lifecycle == "opening" else self.lifecycle
        identity = dict(getattr(self.models, "identity", {}))
        if self.effective_settings is not None:
            identity["operator_settings"] = dict(self.effective_settings)
        if "warm_readiness" in identity:
            from .readiness import resident_identity

            identity["live_residency"] = resident_identity(self.models)
        self.emit(
            "session_ready",
            models=identity,
            control_vad=getattr(vad, "identity", {}),
        )
        self.recognition_task = self.task(self.recognition.run())
        pending = bytearray()
        try:
            while True:
                item = await self.input_queue.get()
                if item is None:
                    if pending:
                        self.padding = CHUNK - len(pending) // 2
                        pending.extend(b"\0\0" * self.padding)
                        await self.block(bytes(pending), vad)
                    await self.recognition.finish_input()
                    await self.recognition_task
                    self.processed = self.received
                    self.input_done.set()
                    # Recognition has retired after all admitted audio/finals.
                    # Silence is completion too: never invent a transcript to
                    # make the host's Finish resolve. Status retains this exact
                    # session-local observation for acknowledgement races.
                    self.input_completion = {
                        "stream_id": self.input_handle,
                        "end_sample": self.cutoff,
                        "processed_end_sample": self.processed,
                        "sequence": self.seq + 1,
                    }
                    self.emit("input_finished", **self.input_completion)
                    return
                pending.extend(item)
                while len(pending) >= CHUNK * 2:
                    block = bytes(pending[: CHUNK * 2])
                    del pending[: CHUNK * 2]
                    await self.block(block, vad)
        finally:
            if not self.recognition_task.done():
                self.recognition_task.cancel()
                await asyncio.gather(self.recognition_task, return_exceptions=True)

    async def block(self, pcm, vad):
        signal = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        probability = await asyncio.get_running_loop().run_in_executor(
            self.control_executor, vad.feed, signal
        )
        if probability >= 0.5:
            self.recognition.note_speech_observed(self.processed + CHUNK)
            if self.current and not self.current.fenced:
                self.fence({"synthesis_id": self.current.id}, cancel=False)
                self.fence({"synthesis_id": self.current.id}, cancel=True)
                self.emit(
                    "interruption_requested",
                    synthesis_id=self.current.id,
                    reason="vad_speech",
                )
        packet = (
            np.concatenate((signal, np.zeros(CHUNK, np.float32)))
            .astype("<f4")
            .tobytes()
        )
        self.recognition.admit(packet, vad_probability=float(probability))
        self.processed = min(self.received, self.processed + CHUNK)

    async def recognition_event(self, message):
        if message.get("type") != "event":
            raise RuntimeError("recognition attempted a non-event effect")
        event = dict(message["event"])
        kind = event.pop("type")
        end = min(self.received, event.get("end_sample", self.received))
        if "end_sample" in event:
            event["end_sample"] = end
        if "start_sample" in event:
            event["start_sample"] = min(event["start_sample"], max(0, end - 1))
        sequence = self.emit(kind, **event)
        if kind == "transcript_final" and self.speaker is not None:
            if sequence is not None:
                self.speaker.submit({**event, "attributes": sequence})

    def feed(self, frame):
        frame.validate()
        if self.lifecycle not in ("opening", "open", "draining") or self.end_seen:
            raise Refused("INPUT_CLOSED", "audio is not admissible")
        if frame.kind == DISCONTINUITY:
            raise Refused("INPUT_GAP", "recognition cannot conceal missing speech")
        if frame.kind == PCM and (not frame.pcm or self.input_signalled):
            raise Refused("INPUT_CLOSED", "empty or post-cutoff PCM")
        if self.input_stream is not None and frame.stream != self.input_stream:
            raise Refused("FOREIGN_STREAM", "input stream changed")
        if self.input_seq is not None and frame.seq != self.input_seq + 1:
            raise Refused("INPUT_SEQUENCE", "duplicate or missing frame")
        if frame.start != self.received:
            raise Refused("INPUT_SPAN", "overlap or gap")
        end = self.received + len(frame.pcm) // 2
        if end - self.recognition.received_position > 32768:
            raise Refused(
                "INPUT_OVERFLOW", "input exceeds 2.048 seconds ahead of recognition"
            )
        if end > 16000 * 1800 or self.cutoff is not None and end > self.cutoff:
            raise Refused("INPUT_CUTOFF", "frame exceeds admitted boundary")
        if frame.kind == END and self.cutoff is not None and end != self.cutoff:
            raise Refused("INPUT_TAIL", "end before required tail")
        if frame.kind == PCM:
            # Data never takes the sentinel's reserved slot.
            if self.input_queue.qsize() >= INPUT_QUEUE_FRAMES:
                raise Refused("INPUT_OVERFLOW", "bounded input queue full")
            self.input_queue.put_nowait(frame.pcm)
        self.input_stream, self.input_seq, self.received = frame.stream, frame.seq, end
        if frame.kind == PCM and self.speaker is not None:
            self.speaker.feed(frame.start, frame.pcm)
        if frame.kind == END:
            self.end_seen = True
        if self.cutoff is not None and self.received == self.cutoff:
            self.end_input()

    def end_input(self):
        if not self.input_signalled:
            # The reserved slot: this put cannot be refused, so a committed
            # cutoff is always followed by its sentinel.
            self.input_queue.put_nowait(None)
            self.input_signalled = True

    def finish(self, args):
        if self.lifecycle not in ("opening", "open"):
            raise Refused("SESSION_STATE", self.lifecycle)
        end = args.get("end_sample")
        if (
            args.get("stream_id") != self.input_handle
            or type(end) is not int
            or end < self.received
            or end > 16000 * 1800
        ):
            raise Refused(
                "INPUT_CUTOFF",
                "exact input handle and future exclusive cutoff required",
            )
        if self.cutoff is not None and self.cutoff != end:
            raise Refused("INPUT_CUTOFF", "cutoff cannot change")
        if self.end_seen and end != self.received:
            raise Refused("INPUT_TAIL", "end already arrived")
        if self.cutoff is None:
            self.cutoff = end
            self.task(self.tail_deadline())
        if self.received == end:
            self.end_input()
        return {"accepted": True, "end_sample": end, "stream_id": self.input_handle}

    async def tail_deadline(self):
        async with asyncio.timeout(self.tail_timeout):
            while not self.input_signalled:
                await asyncio.sleep(0.01)

    def synthesize(self, args):
        if self.lifecycle != "open":
            raise Refused("NOT_READY", self.lifecycle)
        sid = identifier(args.get("synthesis_id"), "synthesis_id")
        if sid in self.synthesis_ids or len(self.synthesis_ids) >= 4096:
            raise Refused("ID_REUSE_OR_LIMIT", "fresh synthesis ID required")
        if self.current and not self.current.terminal:
            raise Refused("SYNTHESIS_BUSY", "prior inference has not retired")
        text = args.get("text")
        split_text(text)
        job = self.output.submit(text)
        self.synthesis_ids.add(sid)
        self.stream_counter += 1
        generation = Generation(sid, self.stream_counter, job)
        self.generations[sid] = self.current = generation
        self.emit(
            "synthesis_start",
            synthesis_id=sid,
            output_stream=generation.stream,
            text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        )
        generation.task = self.task(self.generate(generation))
        return {
            "accepted": True,
            "synthesis_id": sid,
            "output_stream": generation.stream,
        }

    async def generate(self, generation):
        job = generation.job
        try:
            while (chunk := await job.read()) is not None:
                if generation.fenced:
                    continue
                pcm = (
                    np.rint(np.clip(chunk.samples, -1, 32767 / 32768) * 32768)
                    .astype("<i2")
                    .tobytes()
                )
                # A write is bounded by the transport. Its admission is NOT playback.
                delivered = await self.write_audio(
                    Frame(
                        PCM,
                        generation.stream,
                        generation.seq,
                        generation.delivered,
                        pcm,
                    ),
                    generation,
                )
                if delivered is False:
                    continue
                generation.seq += 1
                generation.delivered += len(pcm) // 2
            await job.wait()
        finally:
            if not job.finished:
                self.output.cancel(job)
                await job.wait()
        await self.write_audio(
            Frame(END, generation.stream, generation.seq, generation.delivered),
            generation,
        )
        generation.terminal = True
        self.emit(
            "synthesis_cancelled" if generation.cancelled else "synthesis_end",
            synthesis_id=generation.id,
            output_stream=generation.stream,
            delivered_samples=generation.delivered,
            generated_samples=job.produced_samples,
            playback_verified=False,
        )

    def fence(self, args, *, cancel):
        sid = args.get("synthesis_id") or (self.current.id if self.current else None)
        generation = self.generations.get(sid)
        if sid is not None and generation is None:
            raise Refused("STALE_SYNTHESIS", "unknown synthesis")
        if generation:
            generation.fenced = True
            if cancel and not generation.terminal:
                generation.cancelled = True
                self.output.cancel(generation.job)
                cancel_backend = getattr(self.models, "cancel_synthesis", None)
                if cancel_backend is not None:
                    cancel_backend()
        return {
            "accepted": True,
            "synthesis_id": sid,
            "output_stream": generation.stream if generation else None,
            "output_fenced": True,
            "playback_verified": False,
        }

    def playback_report(self, sid, stream, rendered, *, session_id, terminal=False):
        """Apply host-validated client evidence on this session's control owner.

        Both local adapters and the playback-report control use this owner.
        The host must bind the browser/session/stream and convert its rendered
        count to the negotiated 24 kHz engine clock before calling it. A
        terminal report asserts the host observed drain or a completed stop;
        neither pipe delivery nor a stop request can manufacture that report.
        """
        if session_id != self.id or self.lifecycle not in ("open", "draining"):
            raise Refused("STALE_SESSION", "render report names no live instance")
        g = self.generations.get(sid) if isinstance(sid, str) else None
        if (
            g is None
            or type(stream) is not int
            or stream != g.stream
            or type(terminal) is not bool
            or type(rendered) is not int
            or rendered < g.reported
            or rendered > g.delivered
        ):
            raise Refused("PLAYBACK_REPORT", "foreign or impossible render progress")
        if g.playback_resolved:
            if terminal and rendered == g.reported:
                return  # exact final retry; no second event or state change
            raise Refused("PLAYBACK_RESOLVED", "terminal render evidence is immutable")
        if terminal and not (g.terminal or g.fenced):
            raise Refused("PLAYBACK_REPORT", "output still live")
        if terminal and not g.fenced and rendered != g.delivered:
            raise Refused("PLAYBACK_REPORT", "complete tail not rendered")
        if rendered == g.reported and not terminal:
            return  # unchanged progress does not mint another state watermark
        g.reported = rendered
        g.playback_resolved = terminal
        self.emit(
            "playback_observation",
            synthesis_id=g.id,
            output_stream=g.stream,
            sample_rate=24000,
            rendered_samples=g.reported,
            delivered_samples=g.delivered,
            discarded_samples=g.delivered - g.reported if terminal else 0,
            terminal=terminal,
            outcome="stopped"
            if terminal and g.fenced
            else "drained"
            if terminal
            else "progress",
            evidence="host_validated_client_report",
            playback_verified=False,
        )
        self.drain_changed.set()

    def close(self, args):
        mode = args.get("mode")
        if mode not in ("abort", "drain"):
            raise Refused("CLOSE_MODE", "drain or abort required")
        if self.lifecycle in ("closed", "failed") or (
            self.lifecycle == "draining"
            and (mode != "abort" or self.failure is not None)
        ):
            raise Refused("SESSION_STATE", self.lifecycle)
        if mode == "drain" and self.cutoff is None:
            raise Refused("INPUT_CUTOFF", "finish_input required before drain")
        if mode == "abort":
            if not self.abort_requested:
                self.fence({}, cancel=True)
                self.abort_requested = True
                self.drain_changed.set()
            if self.lifecycle == "draining":
                # Upgrade the existing close owner, including before it first
                # runs. Never cancel its await or spawn competing cleanup.
                return {"accepted": True, "mode": mode}
        self.lifecycle = "draining"
        self.task(self.release(mode))
        return {"accepted": True, "mode": mode}

    async def release(self, mode):
        if mode == "drain":
            async with asyncio.timeout(self.drain_timeout):
                while not self.abort_requested:
                    self.drain_changed.clear()
                    if (
                        self.input_done.is_set()
                        and (self.speaker is None or not self.speaker.pending)
                        and (
                            self.current is None
                            or (self.current.terminal and self.current.task.done())
                        )
                        and all(g.playback_resolved for g in self.generations.values())
                    ):
                        break
                    await self.drain_changed.wait()
        if not self.abort_requested:
            await self.output.close()
            if self.speaker is not None:
                await self.speaker.close()
        # An admitted Abort wins until terminal publication, including while
        # output.close was retiring. It never manufactures playback receipts.
        if self.abort_requested:
            mode = "abort"
            errors = await self.abort_resources()
            if errors:
                raise RuntimeError("engine abort errors: " + "; ".join(errors))
        # No terminal state/event until this release task and every other owner
        # have retired through task().finished. Publishing here made immediate
        # reuse observe "closed" but fail BUSY on this still-live release task.
        self.pending_end = {
            "status": "completed" if mode == "drain" else "aborted",
            "scope": "engine_resources",
            "playback_verified": False,
            "input_samples": self.received,
            "model_padding_samples": self.padding,
        }

    async def abort_resources(self):
        """Retire owned async work AND uncancellable executor calls before release."""
        current = asyncio.current_task()
        if self.speaker is not None:
            # Stop publication immediately; retirement is joined below without
            # holding up the already-admitted stop/cancel controls.
            self.speaker.closed = True
        if self.recognition is not None:
            self.recognition.abort()
        generation_task = self.current.task if self.current else None
        retiring = [t for t in tuple(self.tasks) if t not in (current, generation_task)]
        for task in retiring:
            task.cancel()
        results = await asyncio.gather(*retiring, return_exceptions=True)
        if generation_task is not None:
            results.extend(
                await asyncio.gather(generation_task, return_exceptions=True)
            )
        if self.output:
            results.extend(
                await asyncio.gather(
                    self.output.close(abort=True), return_exceptions=True
                )
            )
        # Cancelling an asyncio await does not stop a native executor call.
        # Each single-owner executor must cross a barrier before the host hears
        # terminal release, including independent VAD and semantic-endpoint work.
        executors = {self.executor, self.control_executor}
        for name in ("endpoint_executor", "recognition_executor"):
            executor = getattr(self.models, name, None)
            if executor is not None:
                executors.add(executor)
        loop = asyncio.get_running_loop()
        await asyncio.gather(
            *(loop.run_in_executor(e, lambda: None) for e in executors)
        )
        if self.retire_audio is not None:
            await self.retire_audio()
        if self.speaker is not None:
            results.extend(
                await asyncio.gather(self.speaker.close(), return_exceptions=True)
            )
        return [str(x) for x in results if isinstance(x, Exception)]

    def status(self):
        g = self.current
        return {
            "session_id": self.id,
            "state_sequence": self.seq,
            "lifecycle": self.lifecycle,
            "reason": self.failure,
            "operator_settings": (
                dict(self.effective_settings)
                if self.effective_settings is not None
                else None
            ),
            "input": {
                "processing": {
                    "source": "browser_reported",
                    "reported": dict(self.capture_processing),
                    "echo_cancellation_verified": False,
                    "engine_echo_cancellation": False,
                } if self.capture_processing is not None else None,
                "state": "finished"
                if self.input_done.is_set()
                else "finishing"
                if self.cutoff is not None
                else "accepting",
                "admitted_end_sample": self.cutoff,
                "processed_end_sample": min(self.received, self.recognition.position),
                "received_end_sample": self.received,
            },
            "recognition": {
                "utterance_open": self.recognition.stream is not None,
                "finalization_pending": self.cutoff is not None
                and not self.input_done.is_set(),
            },
            "input_completion": (
                dict(self.input_completion) if self.input_completion else None
            ),
            "synthesis": {
                "synthesis_id": g.id if g else "",
                "state": g.job.snapshot()["state"] if g else "idle",
            },
            "playback": {
                "synthesis_id": g.id if g else "",
                "state": "unobserved"
                if any(not x.playback_resolved for x in self.generations.values())
                else "idle",
                "queued_samples": sum(
                    x.delivered - x.reported
                    for x in self.generations.values()
                    if not x.playback_resolved
                ),
                "rendered_samples": sum(x.reported for x in self.generations.values()),
                "discarded_samples": sum(
                    x.delivered - x.reported
                    for x in self.generations.values()
                    if x.playback_resolved
                ),
                "delivered_samples": sum(
                    x.delivered for x in self.generations.values()
                ),
                "sample_rate": 24000,
                "evidence": "client_reports_not_acoustic_measurements",
            },
        }

    async def shutdown(self):
        if self.speaker is not None:
            self.speaker.closed = True
        if self.output:
            self.fence({}, cancel=True)
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        if self.output:
            await self.output.close(abort=True)
        if self.speaker is not None:
            await self.speaker.close()
