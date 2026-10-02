"""Causal, sample-accounted native PCM boundary; no model feature transform."""

from __future__ import annotations

import base64
import hashlib

import numpy as np
from scipy.signal import firwin, lfilter


class Decimator48k:
    """48k->16k causal FIR, retained phase/state, explicit 126-sample tail."""

    def __init__(self):
        self.taps = firwin(127, 7200, fs=48000).astype(np.float64)
        self.state = np.zeros(126)
        self.position = 0
        self.ended = False

    def push(self, values):
        if self.ended:
            raise ValueError("resampler already ended")
        values = np.asarray(values, dtype=np.float32)
        if values.ndim != 1 or not np.isfinite(values).all():
            raise ValueError("invalid resampler PCM")
        if not len(values):
            return np.empty(0, dtype=np.float32)
        filtered, self.state = lfilter(self.taps, [1.0], values, zi=self.state)
        start = (-self.position) % 3
        self.position += len(values)
        return filtered[start::3].astype(np.float32)

    def finish(self):
        # Flush the full FIR support, not only its group delay. Preserve the
        # last captured phoneme; the extra 126 input samples are declared.
        result = self.push(np.zeros(126, dtype=np.float32))
        self.ended = True
        return result


class NativePCM:
    """Validate native chunks and align reference to microphone host time.

    Apple VPIO has already processed the microphone. Reference alignment is
    for evidence/control, not a second unqualified echo-removal pass.
    """

    def __init__(self, ready):
        if ready["input_sample_rate"] != 48000 or ready["render_sample_rate"] != 48000:
            raise ValueError("native bridge requires declared 48k input/reference")
        if not ready["voice_processing_enabled"] or ready["voice_processing_bypassed"]:
            raise ValueError("native bridge requires active Apple voice processing")
        self.frequency = ready["host_tick_frequency"]
        if (
            not isinstance(self.frequency, (float, int))
            or not np.isfinite(self.frequency)
            or self.frequency <= 0
        ):
            raise ValueError("invalid native host tick frequency")
        self.first = {}
        self.positions = {"microphone": 0, "render": 0}
        self.last = {}
        self.buffers = {name: np.empty(0, dtype=np.float32) for name in self.positions}
        self.resamplers = {name: Decimator48k() for name in self.positions}
        self.offset = None
        self.discard_reference = 0
        self.ended = False
        self.fast_buffer = np.empty(0, dtype=np.float32)

    def push(self, row):
        name = row["stream"]
        if self.ended or name not in self.positions or row["sample_rate"] != 48000:
            raise ValueError("unexpected native stream/rate or audio after end")
        count = row["frames"]
        if (
            type(count) is not int
            or not 0 < count <= 8192
            or row["start_sample"] != self.positions[name]
        ):
            raise ValueError("native sample discontinuity")
        raw = base64.b64decode(row["pcm_f32le"], validate=True)
        if (
            len(raw) != count * 4
            or hashlib.sha256(raw).hexdigest() != row["content_sha256"]
        ):
            raise ValueError("native PCM identity differs")
        values = np.frombuffer(raw, dtype="<f4")
        if not np.isfinite(values).all() or np.max(np.abs(values)) > 1.001:
            raise ValueError("invalid native PCM")
        if name in self.last:
            prior = self.last[name]
            if (
                row["hardware_sample_time"]
                != prior["hardware_sample_time"] + prior["frames"]
            ):
                raise ValueError("native hardware sample clock jumped")
            expected_ticks = (
                prior["hardware_host_ticks"] + prior["frames"] * self.frequency / 48000
            )
            if (
                abs(row["hardware_host_ticks"] - expected_ticks)
                > self.frequency * 0.001
            ):
                raise ValueError("native hardware clock drift exceeds 1ms per callback")
        self.first.setdefault(name, row["hardware_host_ticks"])
        self.last[name] = row
        self.positions[name] += count
        # Retain both streams and decimation phase across callback boundaries.
        # Pairing waits for actual reference coverage, never invented samples.
        if name == "microphone":
            output = self.resamplers[name].push(values)
            self.buffers[name] = np.concatenate((self.buffers[name], output))
            self.fast_buffer = np.concatenate((self.fast_buffer, output))
        else:
            self.buffers[name] = np.concatenate((self.buffers[name], values))
        if len(self.buffers[name]) > 96000:
            raise ValueError("native alignment backlog exceeded two seconds")
        return self._paired()

    def fast_blocks(self):
        """Microphone-only control blocks; reference cannot delay VAD."""
        result = []
        while len(self.fast_buffer) >= 512:
            result.append(self.fast_buffer[:512].copy())
            self.fast_buffer = self.fast_buffer[512:]
        return result

    def can_finish(self):
        return (
            self.offset is not None
            and self.resamplers["render"].position
            >= self.resamplers["microphone"].position
        )

    def _paired(self, final=False):
        if len(self.first) != 2:
            return []
        if self.offset is None:
            difference = (
                (self.first["render"] - self.first["microphone"])
                * 48000
                / self.frequency
            )
            if abs(difference) > 4800:
                raise ValueError("native stream epochs differ by more than 100ms")
            self.offset = round(difference)
            self.discard_reference = max(0, -self.offset)
            if self.offset > 0:
                self.buffers["render"] = np.concatenate(
                    (np.zeros(self.offset, dtype=np.float32), self.buffers["render"])
                )
            self.reference16 = np.empty(0, dtype=np.float32)
        ref = self.buffers["render"]
        skip = min(len(ref), self.discard_reference)
        self.discard_reference -= skip
        ref = ref[skip:]
        self.buffers["render"] = np.empty(0, dtype=np.float32)
        self.reference16 = np.concatenate(
            (self.reference16, self.resamplers["render"].push(ref))
        )
        if final:
            self.reference16 = np.concatenate(
                (self.reference16, self.resamplers["render"].finish())
            )
        result = []
        while min(len(self.buffers["microphone"]), len(self.reference16)) >= 512:
            result.append(
                (self.buffers["microphone"][:512].copy(), self.reference16[:512].copy())
            )
            self.buffers["microphone"] = self.buffers["microphone"][512:]
            self.reference16 = self.reference16[512:]
        if len(self.reference16) > 32000 or len(self.buffers["microphone"]) > 32000:
            raise ValueError("aligned model input backlog exceeded two seconds")
        return result

    def finish(self):
        if self.ended or len(self.first) != 2:
            raise ValueError("cannot finish incomplete/ended native streams")
        tail = self.resamplers["microphone"].finish()
        self.buffers["microphone"] = np.concatenate((self.buffers["microphone"], tail))
        self.fast_buffer = np.concatenate((self.fast_buffer, tail))
        blocks = self._paired(final=True)
        count = len(self.buffers["microphone"])
        if count > 512 or len(self.reference16) < count:
            raise ValueError("reference does not cover final microphone samples")
        padding = 0
        if count:
            padding = 512 - count
            blocks.append(
                (
                    np.pad(self.buffers["microphone"], (0, padding)),
                    np.pad(self.reference16[:count], (0, padding)),
                )
            )
            self.fast_buffer = np.pad(self.fast_buffer, (0, padding))
        self.ended = True
        return blocks, {
            "native_microphone_samples": self.positions["microphone"],
            "native_reference_samples": self.positions["render"],
            "reference_offset_48k_samples": self.offset,
            "fir_taps": 127,
            "fir_group_delay_48k_samples": 63,
            "fir_tail_48k_samples": 126,
            "final_16k_padding_samples": padding,
        }
