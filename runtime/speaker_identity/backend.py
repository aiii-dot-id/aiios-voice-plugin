"""Pinned WeSpeaker inference and shared Kaldi-compatible preprocessing.

Frames are utterance-CMN features, partitioned into fixed two-second windows.
A final overlapping window retains the tail. This is a final-utterance API,
not a claim of causal streaming embeddings or two-second response latency.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import os
import stat
import threading
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .identity import SpeakerIdentityError, digest, unit

MODEL_SHA256 = "33af8affe6191b1ebd196d2b56e22c2934104cd2764abfdbdd954d3a934eb2a1"
FRAMES = 198
MAX_SECONDS = 30
MIN_SAMPLES = 400 + (FRAMES - 1) * 160
MAX_WAV_BYTES = 16000 * MAX_SECONDS * 2 + 65536
FRONTEND = {
    "sample_rate": 16000,
    "bins": 80,
    "frame_ms": 25,
    "shift_ms": 10,
    "window": "hamming",
    "dither": 0,
    "use_energy": False,
    "pcm_scale": "signed_pcm16",
    "cmn": "whole_utterance",
    "window_frames": FRAMES,
    "tail": "last_overlapping_window_no_padding",
    "aggregation": "unit_mean_of_unit_window_embeddings",
    "maximum_seconds": MAX_SECONDS,
    "minimum_samples": MIN_SAMPLES,
}


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_pcm16(path: Path) -> tuple[np.ndarray, int]:
    # Test the opened file and bound the declared frames before allocating PCM.
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_WAV_BYTES:
            raise SpeakerIdentityError("UID requires a bounded regular WAV file")
        return _decode_pcm16(source)


def read_pcm16_wav(audio: bytes) -> tuple[np.ndarray, int]:
    """The same bounded decoder for immutable session audio, without disk I/O."""
    if not isinstance(audio, bytes) or not 44 <= len(audio) <= MAX_WAV_BYTES:
        raise SpeakerIdentityError("UID requires bounded immutable WAV bytes")
    return _decode_pcm16(io.BytesIO(audio))


def _decode_pcm16(source) -> tuple[np.ndarray, int]:
    with wave.open(source, "rb") as handle:
        count = handle.getnframes()
        if (
            handle.getnchannels() != 1
            or handle.getsampwidth() != 2
            or handle.getframerate() != 16000
        ):
            raise SpeakerIdentityError("UID requires 16 kHz mono PCM16 WAV")
        if not MIN_SAMPLES <= count <= MAX_SECONDS * 16000:
            raise SpeakerIdentityError(
                "UID needs 1.995..30 seconds of utterance audio; no silent cropping"
            )
        raw = handle.readframes(count)
    if len(raw) != count * 2:
        raise SpeakerIdentityError("truncated WAV payload")
    values = np.frombuffer(raw, dtype="<i2").astype(np.float32)
    rms = float(np.sqrt(np.mean(np.square(values / 32768))))
    if rms < 0.0003 or np.mean(np.abs(values) >= 32760) > 0.1:
        raise SpeakerIdentityError("silent/near-silent or heavily clipped UID audio")
    return values, 16000


def fbank_from_samples(samples: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
    import kaldi_native_fbank as knf

    options = knf.FbankOptions()
    options.frame_opts.samp_freq = sample_rate
    options.frame_opts.dither = 0.0
    options.frame_opts.frame_length_ms = 25.0
    options.frame_opts.frame_shift_ms = 10.0
    options.frame_opts.window_type = "hamming"
    options.mel_opts.num_bins = 80
    options.use_energy = False
    extractor = knf.OnlineFbank(options)
    extractor.accept_waveform(sample_rate, samples.tolist())
    extractor.input_finished()
    if extractor.num_frames_ready <= 0:
        raise SpeakerIdentityError("filterbank produced no frames")
    features = np.stack(
        [extractor.get_frame(i) for i in range(extractor.num_frames_ready)]
    ).astype(np.float32)
    features -= features.mean(axis=0, keepdims=True)
    return np.ascontiguousarray(features[None], dtype=np.float32)


def fbank_features(path: Path) -> np.ndarray:
    return fbank_from_samples(*read_pcm16(path))


def windows(features: np.ndarray):
    if (
        features.ndim != 3
        or features.shape[0] != 1
        or features.shape[2] != 80
        or features.shape[1] < FRAMES
        or not np.isfinite(features).all()
    ):
        raise SpeakerIdentityError("invalid UID feature shape/values")
    size = features.shape[1]
    if size > MAX_SECONDS * 100:
        raise SpeakerIdentityError("UID feature sequence exceeds bound")
    starts = list(range(0, size - FRAMES + 1, FRAMES))
    if starts[-1] != size - FRAMES:
        starts.append(size - FRAMES)
    return [np.ascontiguousarray(features[:, i : i + FRAMES]) for i in starts]


@dataclass(frozen=True)
class Embedding:
    vector: np.ndarray
    embedding_binding: str
    audio_sha256: str
    seconds: float
    windows: int


class WeSpeaker:
    """One resident model; callers put this synchronous work on a bounded worker.

    `coreml` requests CPUAndGPU. Placement needs profile/compute-plan evidence;
    get_providers() alone must never be reported as proof of GPU execution.
    Missing requested providers fail rather than silently choosing a new one.
    """

    def __init__(
        self,
        source: Path,
        model: Path,
        specialization: Path,
        *,
        provider: str,
        profile: Path | None = None,
    ):
        from runtime.onnx_runtime import local_runtime

        ort = local_runtime()

        manifest = json.loads(Path(specialization).read_text())
        if (
            file_sha256(source) != MODEL_SHA256
            or manifest["source_model_sha256"] != MODEL_SHA256
        ):
            raise SpeakerIdentityError("WeSpeaker source model identity differs")
        if (
            manifest["input_frames"] != FRAMES
            or manifest["input_shape"] != [1, FRAMES, 80]
            or file_sha256(model) != manifest["specialized_model_sha256"]
        ):
            raise SpeakerIdentityError(
                "WeSpeaker specialization identity/shape differs"
            )
        self.binding_record = {
            "source_sha256": MODEL_SHA256,
            "model_sha256": manifest["specialized_model_sha256"],
            "frontend": FRONTEND,
            "kaldi_native_fbank": importlib.metadata.version("kaldi-native-fbank"),
        }
        self.binding = digest(self.binding_record)
        names = {
            "cpu": "CPUExecutionProvider",
            "coreml": "CoreMLExecutionProvider",
            "cuda": "CUDAExecutionProvider",
            "directml": "DmlExecutionProvider",
        }
        if (
            provider not in names
            or names[provider] not in ort.get_available_providers()
        ):
            raise SpeakerIdentityError(
                f"requested UID provider unavailable: {provider}"
            )
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        if profile:
            options.enable_profiling = True
            options.profile_file_prefix = str(profile)
        provider_options = {}
        if provider == "coreml":
            provider_options = {
                "MLComputeUnits": "CPUAndGPU",
                "ModelFormat": "MLProgram",
                "RequireStaticInputShapes": "1",
                "EnableOnSubgraphs": "1",
                "ProfileComputePlan": "1" if profile else "0",
            }
        if provider == "cuda":
            if hasattr(ort, "preload_dlls"):
                ort.preload_dlls(directory="")
            provider_options = {"use_tf32": "0"}
        if provider == "directml":
            options.enable_mem_pattern = False
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(
            str(model),
            sess_options=options,
            providers=[(names[provider], provider_options)],
        )
        self.session.disable_fallback()
        if self.session.get_providers()[0] != names[provider]:
            raise SpeakerIdentityError(
                "runtime silently changed requested UID provider"
            )
        if [(i.name, i.shape) for i in self.session.get_inputs()] != [
            ("feats", [1, FRAMES, 80])
        ]:
            raise SpeakerIdentityError("runtime input shape differs")
        self.lock = threading.Lock()
        self.runtime = {
            "requested_provider": names[provider],
            "registered_providers": self.session.get_providers(),
            "options": provider_options,
            "onnxruntime": ort.__version__,
            "gpu_execution_proven": False,
        }

    def embed(self, path: Path) -> Embedding:
        return self._embed_samples(*read_pcm16(path))

    def embed_wav(self, audio: bytes) -> Embedding:
        return self._embed_samples(*read_pcm16_wav(audio))

    def _embed_samples(self, samples, rate) -> Embedding:
        chunks = windows(fbank_from_samples(samples, rate))
        # Identify the exact decoded samples used, not a path read a second time.
        audio_sha = hashlib.sha256(samples.astype("<i2").tobytes()).hexdigest()
        with self.lock:
            embeddings = [
                unit(self.session.run(["embs"], {"feats": c})[0][0]) for c in chunks
            ]
        vector = unit(np.mean(embeddings, axis=0))
        vector.setflags(write=False)
        return Embedding(
            vector, self.binding, audio_sha, len(samples) / rate, len(chunks)
        )

    def end_profile(self) -> str:
        with self.lock:
            return self.session.end_profiling()
