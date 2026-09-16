"""Bounded concurrent PCM ingress for the pinned Transformers streaming RNN-T.

Control never waits for inference. The generation owner alone touches model
state. End-of-input and cancellation are different terminal conditions.
"""
from __future__ import annotations

import threading
import time

import numpy as np


class StreamCancelled(Exception):
    pass


def mel_span(frame: int, count: int, hop: int, fft: int) -> tuple[int, int]:
    """Half-open source window supporting exactly these centered STFT frames."""
    if frame < 0 or count <= 0 or hop <= 0 or fft <= 0 or fft % 2:
        raise ValueError("invalid STFT span")
    return frame * hop - fft // 2, (frame + count - 1) * hop + fft // 2


class PCMIngress:
    def __init__(self, *, capacity: int = 16000 * 20):
        if type(capacity) is not int or capacity <= 0:
            raise ValueError("positive bounded sample capacity required")
        self._pcm = np.empty(capacity, dtype=np.float32)
        self._condition = threading.Condition()
        self.samples = 0
        self.finished = False
        self.cancelled = threading.Event()
        self.spans: list[dict] = []

    def push(self, values) -> None:
        chunk = np.asarray(values, dtype=np.float32)
        if chunk.ndim != 1 or not len(chunk) or not np.isfinite(chunk).all():
            raise ValueError("finite nonempty mono PCM required")
        with self._condition:
            if self.finished or self.cancelled.is_set():
                raise RuntimeError("input is closed")
            end = self.samples + len(chunk)
            if end > len(self._pcm):
                raise OverflowError("audio capacity exceeded")
            self._pcm[self.samples:end] = chunk
            self.samples = end
            self._condition.notify_all()

    def finish(self) -> None:
        with self._condition:
            if self.finished or self.cancelled.is_set():
                raise RuntimeError("input is closed")
            if not self.samples:
                raise ValueError("cannot finish empty input")
            self.finished = True
            self._condition.notify_all()

    def cancel(self) -> None:
        self.cancelled.set()
        with self._condition:
            self._condition.notify_all()

    def chunks(self, *, first_frames: int, next_frames: int, hop: int, fft: int,
               wait_seconds: float = 10):
        if min(first_frames, next_frames, hop, fft) <= 0 or wait_seconds <= 0:
            raise ValueError("positive chunk geometry and timeout required")
        frame = 0
        while True:
            count = first_frames if frame == 0 else next_frames
            start, end = mel_span(frame, count, hop, fft)
            with self._condition:
                ready = self._condition.wait_for(
                    lambda boundary=end: self.cancelled.is_set() or self.finished or self.samples >= boundary,
                    timeout=wait_seconds,
                )
                if self.cancelled.is_set():
                    raise StreamCancelled("cancelled before next acoustic chunk")
                if not ready:
                    raise TimeoutError("audio input stalled")
                if self.finished and frame >= self.samples // hop + 1:
                    return
                # The first processor call supplies center=True. Later calls
                # supply their own left/right zero pad and center=False.
                left = 0 if frame == 0 else max(0, -start)
                begin = max(0, start)
                actual_end = min(end, self.samples)
                pcm = self._pcm[begin:actual_end].copy()
                right = max(0, end - self.samples)
                pcm = np.pad(pcm, (left, right))
                record = {"mel_start": frame, "mel_frames": count,
                          "source_start": start, "source_end": end,
                          "samples_admitted": self.samples,
                          "left_pad": left, "right_pad": right,
                          "final_input": self.finished}
                self.spans.append(record)
            yield pcm, frame == 0, count
            frame += count


def feature_stream(ingress: PCMIngress, processor, *, device, dtype):
    """Lazy features: no complete-waveform preprocessing before generation."""
    for pcm, first, frames in ingress.chunks(
        first_frames=processor.num_mel_frames_first_audio_chunk,
        next_frames=processor.num_mel_frames_per_audio_chunk,
        hop=processor.feature_extractor.hop_length,
        fft=processor.feature_extractor.n_fft,
    ):
        features = processor(
            pcm, sampling_rate=16000, is_streaming=True,
            is_first_audio_chunk=first, language="en-US", return_tensors="pt",
        ).input_features[:, :frames, :]
        if features.shape[1] != frames:
            raise ValueError(f"processor returned {features.shape[1]}, expected {frames}")
        yield features.to(device=device, dtype=dtype)


def feed_replay(ingress: PCMIngress, pcm: np.ndarray, *, batch_samples=512, paced=False):
    started = time.monotonic()
    for offset in range(0, len(pcm), batch_samples):
        if ingress.cancelled.is_set():
            return
        end = min(offset + batch_samples, len(pcm))
        if paced:
            wait = started + end / 16000 - time.monotonic()
            if wait > 0 and ingress.cancelled.wait(wait):
                return
        ingress.push(pcm[offset:end])
    ingress.finish()
