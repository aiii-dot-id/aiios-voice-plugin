"""Reusable synthesis control, independent of inference and audio-device policy.

All public control methods run on one asyncio loop. One supplied executor owns
the model. Audio delivery is separate from synthesis completion and physical
playback: the embedding host must stop its playback endpoint on interruption.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass

import numpy as np


def split_text(text: str, limit: int = 180) -> tuple[str, ...]:
    """Partition at sentence/word boundaries without losing any input character."""
    if not isinstance(text, str) or not text.strip() or len(text) > 8000:
        raise ValueError("text must contain 1..8000 characters of nonempty speech")
    if any(ord(c) < 32 and c not in "\n\r\t" for c in text):
        raise ValueError("control character in speech text")
    if type(limit) is not int or not 32 <= limit <= 512:
        raise ValueError("segment limit must be within 32..512")
    text.encode("utf-8", errors="strict")
    parts = []
    while len(text) > limit:
        sentences = list(re.finditer(r"[.!?][\"'’”)]*\s+|\n+", text[:limit]))
        spaces = list(re.finditer(r"\s+", text[:limit]))
        choices = sentences or spaces
        if not choices:
            raise ValueError("unbroken word exceeds segment limit")
        end = choices[-1].end()
        parts.append(text[:end])
        text = text[end:]
    if text:
        parts.append(text)
    # Whitespace remains in the exact partition; the backend receives the
    # nonempty stripped segment. No word, punctuation or final tail is removed.
    return tuple(parts)


@dataclass(frozen=True)
class AudioChunk:
    synthesis_id: str
    sequence: int
    start_sample: int
    sample_rate: int
    samples: np.ndarray


class Synthesis:
    def __init__(self, owner, sid, text, segments):
        self.owner, self.id, self.text, self.segments = owner, sid, text, segments
        self.queue = deque()
        self.changed = asyncio.Event()
        self.space = asyncio.Event()
        self.task = None
        self.reader = None
        self.cancel_requested = False
        self.finished = False
        self.error = None
        self.reason = None
        self.produced_samples = self.delivered_samples = self.queued_samples = 0
        self.discarded_samples = self.peak_queued_samples = self.sequence = 0
        self.completed_segments = 0
        self.accepted_at = time.monotonic()
        self.first_audio_at = self.finished_at = None

    def snapshot(self):
        state = (
            "failed"
            if self.error
            else "cancelled"
            if self.finished and self.cancel_requested
            else "cancelling"
            if self.cancel_requested
            else "draining"
            if self.finished and self.queued_samples
            else "completed"
            if self.finished
            else "synthesizing"
        )
        return {
            "synthesis_id": self.id,
            "state": state,
            "generation_finished": self.finished,
            "segments": len([s for s in self.segments if s.strip()]),
            "completed_segments": self.completed_segments,
            "produced_samples": self.produced_samples,
            "delivered_samples": self.delivered_samples,
            "queued_samples": self.queued_samples,
            "discarded_samples": self.discarded_samples,
            "peak_queued_samples": self.peak_queued_samples,
            "sample_rate": self.owner.sample_rate,
            "accepted_at": self.accepted_at,
            "first_audio_at": self.first_audio_at,
            "finished_at": self.finished_at,
            "error": str(self.error) if self.error else None,
            "playback_state": "host_owned_not_observed",
        }

    async def read(self):
        """One consumer; returns None at delivery end, raises on synthesis failure."""
        current = asyncio.current_task()
        if self.reader not in (None, current):
            raise RuntimeError("one audio consumer per synthesis")
        self.reader = current
        while True:
            if self.cancel_requested:
                return None
            if self.queue:
                chunk = self.queue.popleft()
                self.queued_samples -= len(chunk.samples)
                self.delivered_samples += len(chunk.samples)
                self.space.set()
                return chunk
            if self.finished:
                if self.error:
                    raise RuntimeError(
                        "synthesis failed: " + str(self.error)
                    ) from self.error
                return None
            self.changed.clear()
            await self.changed.wait()

    async def wait(self):
        """Waits for model cleanup, not for a DAC or consumer to drain audio."""
        await asyncio.shield(self.task)
        if self.error:
            raise RuntimeError("synthesis failed: " + str(self.error)) from self.error
        return self.snapshot()


class SpeechOutput:
    def __init__(
        self,
        backend,
        executor,
        *,
        sample_rate=24000,
        queue_seconds=2.0,
        consumer_timeout=15.0,
        segment_chars=180,
    ):
        if sample_rate != 24000 or not 0.02 <= queue_seconds <= 5:
            raise ValueError("24kHz output and bounded 0.02..5 second queue required")
        if not 0.1 <= consumer_timeout <= 60:
            raise ValueError("consumer timeout must be within 0.1..60 seconds")
        self.backend, self.executor = backend, executor
        self.sample_rate = sample_rate
        self.queue_limit = int(sample_rate * queue_seconds)
        self.consumer_timeout, self.segment_chars = consumer_timeout, segment_chars
        self.active = None
        self.closed = False
        self.prefix, self.counter = uuid.uuid4().hex, 0
        self.loop = asyncio.get_running_loop()

    def _control(self):
        if asyncio.get_running_loop() is not self.loop:
            raise RuntimeError("controls must use the service event loop")

    def submit(self, text):
        """Admit immediately; never calls the model or waits for its executor."""
        self._control()
        if self.closed:
            raise RuntimeError("speech output is closed")
        if self.active and (not self.active.finished or self.active.queued_samples):
            raise RuntimeError("prior synthesis is still running or draining")
        segments = split_text(text, self.segment_chars)
        self.counter += 1
        job = Synthesis(self, f"{self.prefix}:{self.counter}", text, segments)
        self.active = job
        job.task = asyncio.create_task(self._generate(job))
        return job

    def cancel(self, job):
        """Fence delivery now; completion waits for the in-flight model call."""
        self._control()
        if job.owner is not self:
            raise ValueError("synthesis belongs to another service")
        # A late cancellation cannot turn a fully delivered natural completion
        # into a cancelled result. Draining audio, however, remains cancellable.
        if not job.finished or job.queued_samples:
            job.cancel_requested = True
            job.reason = "cancelled"
            job.discarded_samples += job.queued_samples
            job.queue.clear()
            job.queued_samples = 0
            job.changed.set()
            job.space.set()
        return {"accepted": True, **job.snapshot()}

    async def close(self, *, abort=False):
        self._control()
        if self.active and self.active.queued_samples and not abort:
            raise RuntimeError("drain queued audio before closing, or use abort")
        self.closed = True
        if self.active:
            if abort:
                self.cancel(self.active)
            await self.active.wait()

    async def _model(self, fn, *args):
        return await self.loop.run_in_executor(self.executor, fn, *args)

    async def _enqueue(self, job, samples):
        offset = 0
        while offset < len(samples) and not job.cancel_requested:
            while job.queued_samples >= self.queue_limit and not job.cancel_requested:
                job.space.clear()
                await asyncio.wait_for(job.space.wait(), self.consumer_timeout)
            if job.cancel_requested:
                return
            n = min(len(samples) - offset, self.queue_limit - job.queued_samples)
            piece = samples[offset : offset + n].copy()
            piece.flags.writeable = False
            chunk = AudioChunk(
                job.id, job.sequence, job.produced_samples, self.sample_rate, piece
            )
            job.sequence += 1
            job.produced_samples += n
            job.queued_samples += n
            job.peak_queued_samples = max(job.peak_queued_samples, job.queued_samples)
            job.queue.append(chunk)
            if job.first_audio_at is None:
                job.first_audio_at = time.monotonic()
            offset += n
            job.changed.set()

    async def _generate(self, job):
        generator = None
        try:
            for segment in job.segments:
                if job.cancel_requested:
                    break
                if not segment.strip():
                    continue
                generator = await self._model(self.backend.tts_stream, segment.strip())
                tokens = samples_seen = 0
                while not job.cancel_requested:
                    result = await self._model(self.backend.tts_next, generator)
                    if job.cancel_requested:
                        break  # In-flight inference cannot cross the cancel fence.
                    if result is None:
                        if not samples_seen or tokens >= self.backend.max_tokens:
                            raise RuntimeError(
                                "empty synthesis or token limit without natural termination"
                            )
                        job.completed_segments += 1
                        break
                    samples, rate, count = result
                    if (
                        rate != self.sample_rate
                        or samples.ndim != 1
                        or not len(samples)
                        or len(samples) > self.sample_rate * 5
                        or not np.isfinite(samples).all()
                        or type(count) is not int
                        or count < 0
                    ):
                        raise RuntimeError("invalid backend audio or token accounting")
                    tokens += count
                    samples_seen += len(samples)
                    await self._enqueue(job, np.asarray(samples, dtype=np.float32))
                await self._model(generator.close)
                generator = None
                if job.cancel_requested:
                    break
        except asyncio.CancelledError:
            job.error = RuntimeError("synthesis worker task was cancelled")
            job.discarded_samples += job.queued_samples
            job.queue.clear()
            job.queued_samples = 0
        except Exception as error:  # noqa: BLE001 - preserve arbitrary backend failure for read/wait.
            job.error = error
            # Never deliver remaining queued PCM as though a failed reply were
            # complete. Already consumed audio is explicitly outside our fence.
            job.discarded_samples += job.queued_samples
            job.queue.clear()
            job.queued_samples = 0
        finally:
            try:
                if generator is not None:
                    await self._model(generator.close)
            except Exception as error:  # noqa: BLE001 - cleanup failure must prohibit success.
                job.error = error
            job.finished = True
            job.finished_at = time.monotonic()
            job.changed.set()
