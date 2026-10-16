"""The admitted Whisper CPU math without Transformers' general wrapper.

Specialized to one 16 kHz, eight-second SmartTurn input. The FFT, Hann window,
matrix product, log and reduction order deliberately remain upstream Torch.
The mel coefficients must be exported from, and bound to, the reference build.
No approximate FFT or model/threshold change is made here.

Arithmetic derived from Hugging Face Transformers WhisperFeatureExtractor
(Apache-2.0, Copyright 2022 The HuggingFace Inc. team) and its normalization
path. See the coefficient receipt for the exact upstream source identity.
"""

import hashlib
from pathlib import Path

import numpy as np

WINDOW = 128000


class TorchWhisperFrontend:
    def __init__(self, coefficients, expected_sha256):
        raw = Path(coefficients).read_bytes()
        if len(raw) != 201 * 80 * 4 or hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ValueError("Whisper coefficient size/hash differs")
        mel = np.frombuffer(raw, dtype="<f4").reshape(201, 80).copy()
        if not np.isfinite(mel).all() or np.any(mel < 0) or not np.any(mel > 0):
            raise ValueError("invalid Whisper mel coefficients")
        import torch

        self.torch = torch
        self.mel = torch.from_numpy(mel).to("cpu", torch.float32)
        self.identity = {"frontend": "bound Whisper Torch CPU math; no Transformers import",
                         "coefficients_sha256": expected_sha256, "torch": torch.__version__,
                         "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}

    def features(self, audio):
        samples = np.asarray(audio, dtype=np.float32)
        if samples.ndim != 1 or not len(samples) or not np.isfinite(samples).all():
            raise ValueError("semantic endpoint requires finite nonempty mono audio")
        samples = samples[-WINDOW:]
        samples = np.pad(samples, (WINDOW - len(samples), 0))
        # Match the wrapper's [samples, channel] normalization, then its exact
        # batch/channel transposition. LEFT padding precedes normalization.
        vector = np.asarray([samples]).T
        normalized = (vector - vector[:WINDOW].mean()) / np.sqrt(vector[:WINDOW].var() + 1e-7)
        waveform = np.stack([normalized], axis=0).transpose(2, 0, 1)[0]
        torch = self.torch
        tensor = torch.from_numpy(waveform).to("cpu", torch.float32)
        window = torch.hann_window(400, device="cpu")
        stft = torch.stft(tensor, 400, 160, window=window, return_complex=True)
        magnitudes = stft[..., :-1].abs() ** 2
        mel_spec = self.mel.T @ magnitudes
        log_spec = torch.clamp(mel_spec, min=1e-10).log10()
        maximum = log_spec.max(dim=2, keepdim=True)[0].max(dim=1, keepdim=True)[0]
        log_spec = torch.maximum(log_spec, maximum - 8.0)
        result = ((log_spec + 4.0) / 4.0).numpy()
        if result.shape != (1, 80, 800) or not np.isfinite(result).all():
            raise ValueError("invalid Whisper feature output")
        return result
