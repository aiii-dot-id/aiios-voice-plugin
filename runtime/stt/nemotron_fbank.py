"""Bounded, Torch-free Nemotron reference log-mel frontend.

The owner supplies the hash-verified model's Slaney mel matrix. Geometry and
numeric rules follow its reference extractor, not generic Kaldi defaults:
400-sample symmetric Hann centered in a 512-point FFT, 160-sample hop,
utterance preemphasis, constant-zero edges and log(energy + 2**-24).
This candidate is not selected by a deployed loader.
"""

import numpy as np


class NemotronFbank:
    def __init__(self, mel_filters, *, capacity=16000 * 61):
        mel = np.asarray(mel_filters, dtype=np.float32)
        if mel.shape != (128, 257) or not np.isfinite(mel).all() or np.any(mel < 0):
            raise ValueError("Finite nonnegative 128x257 mel matrix required")
        if type(capacity) is not int or not 512 <= capacity <= 16000 * 61:
            raise ValueError("Bounded PCM capacity required")
        self.mel = mel.copy()
        self.mel.flags.writeable = False
        self.window = np.pad(np.hanning(400).astype(np.float32), (56, 56))
        self.pcm = np.empty(capacity, dtype=np.float32)
        self.samples = 0
        self.finished = False

    def accept_waveform(self, rate, samples):
        values = np.asarray(samples, dtype=np.float32)
        if self.finished:
            raise RuntimeError("Feature input is closed")
        if (
            rate != 16000
            or values.ndim != 1
            or not 0 < len(values) <= 32000
            or not np.isfinite(values).all()
        ):
            raise ValueError("Bounded finite mono/16k batch required")
        end = self.samples + len(values)
        if end > len(self.pcm):
            raise OverflowError("Feature input capacity exceeded")
        # Preemphasis happens before any STFT padding, retaining the preceding
        # real sample across arbitrary audio-batch boundaries.
        self.pcm[self.samples : end] = values
        self.samples = end

    def input_finished(self):
        if self.finished or not self.samples:
            raise RuntimeError("Cannot finish empty or closed input")
        self.finished = True

    @property
    def frames_ready(self):
        if self.finished:
            return self.samples // 160
        return max(0, (self.samples - 256) // 160 + 1)

    def get_frames(self, first, count):
        if (
            type(first) is not int
            or type(count) is not int
            or first < 0
            or not 0 < count <= 128
            or first + count > self.frames_ready
        ):
            raise ValueError("Requested feature frames are unavailable or unbounded")
        indices = (first + np.arange(count))[:, None] * 160 - 256 + np.arange(512)
        valid = (indices >= 0) & (indices < self.samples)
        current = self.pcm[np.clip(indices, 0, self.samples - 1)]
        prior = self.pcm[np.clip(indices - 1, 0, self.samples - 1)]
        values = np.where(indices == 0, current, current - np.float32(0.97) * prior)
        values = np.where(valid, values, np.float32(0))
        spectrum = np.fft.rfft(values * self.window, n=512, axis=-1)
        power = np.square(spectrum.real) + np.square(spectrum.imag)
        energy = power.astype(np.float32) @ self.mel.T
        return np.log(energy + np.float32(2**-24)).astype(np.float32)
