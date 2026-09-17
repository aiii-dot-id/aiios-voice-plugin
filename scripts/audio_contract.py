"""Canonical in-memory audio boundary for Voice Frontier experiments."""

from __future__ import annotations

import hashlib
import io
import math
import struct
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class NormalizedAudio:
    samples: np.ndarray
    sample_rate: int
    evidence: dict[str, Any]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sample_sha256(samples: np.ndarray) -> str:
    canonical = np.asarray(samples, dtype="<f4", order="C")
    return hashlib.sha256(canonical.tobytes()).hexdigest()


def _decode_pcm(payload: bytes, sample_width: int) -> np.ndarray:
    if sample_width == 1:
        return (np.frombuffer(payload, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    if sample_width == 2:
        return np.frombuffer(payload, dtype="<i2").astype(np.float32) / 32768.0
    if sample_width == 3:
        raw = np.frombuffer(payload, dtype=np.uint8).reshape(-1, 3)
        values = raw[:, 0].astype(np.int32) | (raw[:, 1].astype(np.int32) << 8) | (raw[:, 2].astype(np.int32) << 16)
        values = (values ^ 0x800000) - 0x800000
        return values.astype(np.float32) / 8388608.0
    if sample_width == 4:
        return np.frombuffer(payload, dtype="<i4").astype(np.float32) / 2147483648.0
    raise ValueError(f"unsupported PCM sample width: {sample_width} bytes")


def decode_ieee_float_wav_bytes(data: bytes) -> tuple[int, np.ndarray]:
    """Decode a bounded little-endian RIFF WAVE with IEEE float samples."""

    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a little-endian RIFF WAVE")
    declared_size = struct.unpack_from("<I", data, 4)[0] + 8
    if declared_size > len(data):
        raise ValueError("RIFF size exceeds available bytes")

    fmt = None
    payload = None
    offset = 12
    while offset + 8 <= declared_size:
        chunk_id, chunk_size = struct.unpack_from("<4sI", data, offset)
        start = offset + 8
        end = start + chunk_size
        if end > declared_size:
            raise ValueError(f"WAV chunk {chunk_id!r} exceeds RIFF size")
        if chunk_id == b"fmt ":
            if fmt is not None:
                raise ValueError("duplicate WAV fmt chunk")
            if chunk_size < 16:
                raise ValueError("WAV fmt chunk is too short")
            fmt = struct.unpack_from("<HHIIHH", data, start)
        elif chunk_id == b"data":
            if payload is not None:
                raise ValueError("duplicate WAV data chunk")
            payload = data[start:end]
        offset = end + (chunk_size & 1)

    if fmt is None or payload is None:
        raise ValueError("WAV requires one fmt chunk and one data chunk")
    format_tag, channels, sample_rate, byte_rate, block_align, bits = fmt
    if format_tag != 3 or bits not in (32, 64):
        raise ValueError(f"not IEEE float32/64 WAV: format={format_tag}, bits={bits}")
    if not 1 <= channels <= 32 or sample_rate <= 0:
        raise ValueError("invalid IEEE-float WAV channels or sample rate")
    expected_align = channels * (bits // 8)
    if block_align != expected_align or byte_rate != sample_rate * expected_align:
        raise ValueError("IEEE-float WAV byte-rate or block alignment is inconsistent")
    if len(payload) % block_align:
        raise ValueError("IEEE-float WAV payload is not frame-aligned")
    dtype = "<f4" if bits == 32 else "<f8"
    decoded = np.frombuffer(payload, dtype=dtype).reshape(-1, channels)
    return sample_rate, decoded


def normalize_frames(samples: np.ndarray, sample_rate: int, target_rate: int) -> tuple[np.ndarray, dict[str, Any]]:
    if sample_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")
    values = np.asarray(samples)
    if values.ndim == 1:
        channels = 1
    elif values.ndim == 2 and 1 <= values.shape[1] <= 32:
        channels = values.shape[1]
        values = values.astype(np.float64).mean(axis=1)
    else:
        raise ValueError(f"expected [frames] or [frames, channels], got {values.shape}")
    values = values.astype(np.float32, copy=False)
    if not np.isfinite(values).all():
        raise ValueError("audio contains NaN or infinity")
    if sample_rate != target_rate and values.size:
        import scipy
        from scipy.signal import resample_poly

        divisor = math.gcd(sample_rate, target_rate)
        values = resample_poly(values, target_rate // divisor, sample_rate // divisor).astype(np.float32)
        resampler = "scipy.signal.resample_poly"
        resampler_version = scipy.__version__
    else:
        values = np.ascontiguousarray(values, dtype=np.float32)
        resampler = "identity"
        resampler_version = None
    evidence = {
        "source_sample_rate": sample_rate,
        "source_channels": channels,
        "target_sample_rate": target_rate,
        "target_channels": 1,
        "resampler": resampler,
        "resampler_version": resampler_version,
        "samples": int(values.size),
        "duration_seconds": values.size / target_rate,
        "sample_sha256_f32le": sample_sha256(values),
    }
    return values, evidence


def normalize_wav_bytes(
    data: bytes, target_rate: int = 16000, *, source_label: str = "<wav-bytes>"
) -> NormalizedAudio:
    try:
        with wave.open(io.BytesIO(data), "rb") as handle:
            if handle.getcomptype() != "NONE":
                raise ValueError(f"compressed WAV is unsupported: {handle.getcomptype()}")
            channels = handle.getnchannels()
            sample_width = handle.getsampwidth()
            sample_rate = handle.getframerate()
            frames = handle.getnframes()
            payload = handle.readframes(frames)
        decoded = _decode_pcm(payload, sample_width)
        if decoded.size != frames * channels:
            raise ValueError("WAV payload length does not match its frame header")
        decoded = decoded.reshape(frames, channels)
        encoding = f"pcm_s{sample_width * 8}le" if sample_width > 1 else "pcm_u8"
    except (wave.Error, EOFError):
        try:
            sample_rate, decoded = decode_ieee_float_wav_bytes(data)
        except Exception as error:
            raise ValueError(f"input is not a supported WAV: {source_label}") from error
        frames = decoded.shape[0]
        encoding = f"ieee_float{decoded.dtype.itemsize * 8}"

    samples, evidence = normalize_frames(decoded, sample_rate, target_rate)
    evidence.update(
        {
            "input_path": source_label,
            "input_sha256": hashlib.sha256(data).hexdigest(),
            "container": "wav",
            "encoding": encoding,
            "source_frames": frames,
        }
    )
    return NormalizedAudio(samples=samples, sample_rate=target_rate, evidence=evidence)


def normalize_wav(path: Path, target_rate: int = 16000) -> NormalizedAudio:
    return normalize_wav_bytes(
        path.read_bytes(), target_rate, source_label=str(path)
    )


def normalize_pcm_wav(path: Path, target_rate: int = 16000) -> NormalizedAudio:
    """Compatibility name for callers predating explicit IEEE-float WAV input."""

    return normalize_wav(path, target_rate)
