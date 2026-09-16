"""Causal render-reference echo processing, independent of MLX inference.

Transport is 512 samples; AEC3 is 160 samples. Carry partial frames across
packets, never pad each packet. No echo-correlation threshold makes a speech
decision: correlation only estimates render delay for the native canceller.
"""

from collections import deque

import numpy as np
from scipy.signal import correlate

RATE = 16000
FRAME = 160
PACKET = 512


class RenderDelay:
    def __init__(self):
        self.near = deque(maxlen=4)
        self.far = deque(maxlen=36)
        self.hint_ms = 0
        self.correlation = 0.0
        self.updates = 0
        self.candidate = None
        self.confirmations = 0

    def observe(self, near, far):
        self.near.append(near.copy())
        self.far.append(far.copy())
        capture = np.concatenate(self.near).astype(np.float64)
        render = np.concatenate(self.far).astype(np.float64)
        width = len(capture)
        render = render[-(width + RATE) :]
        cumulative = np.r_[0.0, np.cumsum(render * render)]
        energy = np.maximum(0, cumulative[width:] - cumulative[:-width])
        divisor = np.sqrt(energy * np.dot(capture, capture))
        scores = np.divide(
            correlate(render, capture, mode="valid", method="fft"),
            divisor,
            out=np.zeros_like(divisor),
            where=(energy / width > 1e-6) & (divisor > 1e-10),
        )
        index = int(np.argmax(np.abs(scores)))
        self.correlation = min(1.0, float(abs(scores[index])))
        if self.correlation >= 0.65:
            hint = round((len(render) - width - index) / 16)
            if self.candidate is not None and abs(hint - self.candidate) <= 8:
                self.confirmations += 1
            else:
                self.confirmations = 1
            self.candidate = hint
            if self.confirmations >= 2 and abs(hint - self.hint_ms) >= 10:
                self.hint_ms = hint
                self.updates += 1
        else:
            self.confirmations = 0
        return self.hint_ms


class EchoFrontend:
    """One session, one owner. Returns zero or more aligned 512-sample blocks.

    The native processor always advances. Outside the one-second render/tail
    window, forward exact microphone PCM so AEC high-pass/startup does not alter
    ordinary speech. Barge-in remains VAD on the resulting near-end signal.
    """

    def __init__(self, processor):
        self.processor = processor
        self.delay = RenderDelay()
        self.near = np.empty(0, np.float32)
        self.far = np.empty(0, np.float32)
        self.ready = np.empty(0, np.float32)
        self.tail = 0
        self.ended = False
        self.padding_samples = 0
        self.history = deque(maxlen=300)  # 2s adaptation + up to 1s render delay
        self.render_ring = np.zeros(RATE + FRAME, np.float32)
        self.render_position = 0
        self.applied_hint = None
        self.warm_starts = 0

    def push(self, near, far):
        if self.ended:
            raise ValueError("echo input after finish")
        if near.shape != (PACKET,) or far.shape != (PACKET,):
            raise ValueError("echo input requires aligned 512-sample packets")
        if not np.isfinite(near).all() or not np.isfinite(far).all():
            raise ValueError("echo input must be finite")
        hint = self.delay.observe(near, far)
        if self.delay.updates and (
            self.applied_hint is None or abs(hint - self.applied_hint) >= 50
        ):
            # An initial delay discovered only after echo arrives is too late
            # for AEC3's startup state. Rebuild ONLY the canceller from bounded
            # past frames with the measured hint. Never replay audio to VAD,
            # ASR, or the client, and never consume future packets.
            self.processor.stream_delay_ms = 0
            self.processor.reset()
            if self.history:
                past_near = np.concatenate([x[0] for x in self.history])
                past_far = np.concatenate([x[1] for x in self.history])
                delay = hint * 16
                aligned = np.pad(past_far, (delay, 0))[: len(past_far)]
                for start in range(
                    max(0, len(past_near) - RATE * 2), len(past_near), FRAME
                ):
                    self.processor.process(
                        past_near[start : start + FRAME], aligned[start : start + FRAME]
                    )
            self.applied_hint = hint
            self.warm_starts += 1
        self.processor.stream_delay_ms = 0
        self.near = np.concatenate((self.near, near))
        self.far = np.concatenate((self.far, far))
        self._process(len(self.near) // FRAME * FRAME)
        return self._take()

    def _process(self, count):
        parts = [self.ready]
        for start in range(0, count, FRAME):
            near = self.near[start : start + FRAME]
            far = self.far[start : start + FRAME]
            # Explicit render alignment keeps long media-element buffering out
            # of AEC3's residual acoustic-path estimator. The ring advances on
            # sample counts, not server wall time or TTS-generation timestamps.
            self.render_ring[self.render_position : self.render_position + FRAME] = far
            self.render_position = (self.render_position + FRAME) % len(
                self.render_ring
            )
            delay = (self.applied_hint or 0) * 16
            indices = (np.arange(FRAME) + self.render_position - FRAME - delay) % len(
                self.render_ring
            )
            cleaned = self.processor.process(near, self.render_ring[indices])
            if cleaned.shape != near.shape or not np.isfinite(cleaned).all():
                raise RuntimeError("AEC returned invalid PCM")
            self.history.append((near.copy(), far.copy()))
            self.tail = (
                RATE if float(np.mean(far * far)) > 1e-6 else max(0, self.tail - FRAME)
            )
            parts.append(cleaned if self.tail else near)
        self.ready = np.concatenate(parts)
        self.near = self.near[count:].copy()
        self.far = self.far[count:].copy()

    def _take(self):
        count = len(self.ready) // PACKET * PACKET
        result = [self.ready[i : i + PACKET].copy() for i in range(0, count, PACKET)]
        self.ready = self.ready[count:].copy()
        return result

    def finish(self):
        if self.ended:
            return []
        self.ended = True
        if len(self.near):
            self.padding_samples = FRAME - len(self.near)
            self.near = np.pad(self.near, (0, self.padding_samples))
            self.far = np.pad(self.far, (0, self.padding_samples))
            self._process(FRAME)
            self.ready = self.ready[: -self.padding_samples]
        result = self._take()
        if len(self.ready):
            raise RuntimeError("AEC transport accounting mismatch")
        return result

    def diagnostics(self):
        return {
            "delay_hint_ms": self.delay.hint_ms,
            "delay_updates": self.delay.updates,
            "reference_correlation": self.delay.correlation,
            "render_tail_samples": self.tail,
            "warm_starts": self.warm_starts,
            "final_padding_samples": self.padding_samples,
        }
