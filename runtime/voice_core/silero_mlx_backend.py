"""Silero MLX VAD adapter for deterministic Voice Core conformance replay."""

from __future__ import annotations

import hashlib
import importlib.metadata
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from .replay import DigestReplayBackend, ReplayRecord, canonical_json

BINDING_SCHEMA = "aiii.voice.backend.silero-mlx-binding"
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def binding_sha256(binding: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(binding)).hexdigest()


class SileroMLXReplayBackend(DigestReplayBackend):
    """Compare stateful Silero outputs with trace-bound VAD observations."""

    def __init__(
        self,
        model_path: Path,
        binding: dict[str, Any],
        *,
        model_loader: Callable[[Path], Any] | None = None,
        evaluator: Callable[..., None] | None = None,
    ) -> None:
        super().__init__()
        self._model_path = model_path
        self._binding = binding
        self._model_loader = model_loader
        self._evaluator = evaluator
        self._model: Any = None
        self._state: Any = None
        self._input_streams: dict[str, dict[str, Any]] = {}
        self._pending: list[tuple[str, int, int, float]] = []
        self._probabilities: list[float] = []
        self._maximum_abs_error = 0.0

    def _validate_binding(self) -> None:
        required = {
            "schema",
            "schema_version",
            "repo_id",
            "revision",
            "model_lfs_sha256",
            "config_sha256",
            "mlx_version",
            "mlx_audio_version",
            "dtype",
            "sample_rate_hz",
            "chunk_samples",
            "probability_abs_tolerance",
        }
        if set(self._binding) != required:
            raise ValueError("Silero backend binding fields differ")
        if (
            self._binding["schema"] != BINDING_SCHEMA
            or self._binding["schema_version"] != 1
            or self._binding["sample_rate_hz"] != 16000
            or self._binding["chunk_samples"] != 512
            or self._binding["dtype"] != "float32"
        ):
            raise ValueError("Silero backend binding is unsupported")
        for field in (
            "repo_id",
            "revision",
            "mlx_version",
            "mlx_audio_version",
        ):
            if not isinstance(self._binding[field], str) or not self._binding[field]:
                raise ValueError(f"Silero binding {field} must be a non-empty string")
        for field in ("model_lfs_sha256", "config_sha256"):
            if SHA256.fullmatch(self._binding[field]) is None:
                raise ValueError(f"Silero binding {field} must be a lowercase SHA-256")
        tolerance = self._binding["probability_abs_tolerance"]
        if (
            isinstance(tolerance, bool)
            or not isinstance(tolerance, (int, float))
            or not 0.0 <= float(tolerance) <= 1e-3
        ):
            raise ValueError("Silero probability tolerance is invalid")

    def start(self, trace: dict[str, Any]) -> None:
        self._validate_binding()
        if trace["system"]["system_manifest_sha256"] != binding_sha256(
            self._binding
        ):
            raise ValueError("trace does not bind the exact Silero backend")
        expected_runtime = {
            "name": "silero-vad-mlx",
            "revision": self._binding["revision"],
            "backend": "mlx",
            "precision": self._binding["dtype"],
            "settings_sha256": self._binding["config_sha256"],
        }
        if trace["system"]["runtime"] != expected_runtime:
            raise ValueError("trace runtime identity differs from the Silero binding")
        self._input_streams = {
            row["stream_id"]: row
            for row in trace["audio_streams"]
            if row["direction"] == "input"
        }
        if self._model_loader is None:
            if (
                file_sha256(self._model_path / "model.safetensors")
                != self._binding["model_lfs_sha256"]
                or file_sha256(self._model_path / "config.json")
                != self._binding["config_sha256"]
            ):
                raise ValueError("Silero model files differ from the backend binding")
            if (
                importlib.metadata.version("mlx") != self._binding["mlx_version"]
                or importlib.metadata.version("mlx-audio")
                != self._binding["mlx_audio_version"]
            ):
                raise ValueError("installed MLX runtime differs from the Silero binding")
            from mlx_audio.vad import load

            self._model_loader = lambda path: load(path, strict=True)
        if self._evaluator is None:
            import mlx.core as mx

            self._evaluator = mx.eval
        self._model = self._model_loader(self._model_path)
        super().start(trace)

    def reset(self, scope: str) -> None:
        super().reset(scope)
        self._state = self._model.initial_state(
            sample_rate=self._binding["sample_rate_hz"]
        )
        self._pending.clear()

    def consume(self, record: ReplayRecord) -> None:
        event = record.event
        if event["type"] == "input_audio":
            stream = self._input_streams[event["stream_id"]]
            if (
                stream["sample_type"] != "pcm_f32le"
                or stream["channels"] != 1
                or stream["sample_rate_hz"] != self._binding["sample_rate_hz"]
            ):
                raise ValueError("Silero replay requires mono 16 kHz float32 input")
            samples = np.frombuffer(record.content, dtype="<f4")
            if len(samples) != self._binding["chunk_samples"]:
                raise ValueError("Silero replay input must be one exact model chunk")
            if not np.isfinite(samples).all():
                raise ValueError("Silero replay input contains NaN or infinity")
            probability, self._state = self._model.feed(
                samples,
                self._state,
                sample_rate=self._binding["sample_rate_hz"],
            )
            self._evaluator(
                probability, self._state.state, self._state.context
            )
            value = float(np.asarray(probability).reshape(-1)[0])
            self._pending.append(
                (event["stream_id"], event["start_sample"], event["end_sample"], value)
            )
        elif event["type"] == "vad_probability":
            if not self._pending:
                raise ValueError("VAD observation has no preceding model output")
            stream_id, start, end, actual = self._pending.pop(0)
            if (stream_id, start, end) != (
                event["stream_id"],
                event["start_sample"],
                event["end_sample"],
            ):
                raise ValueError("VAD observation span differs from model input")
            error = abs(actual - float(event["probability"]))
            self._maximum_abs_error = max(self._maximum_abs_error, error)
            if error > float(self._binding["probability_abs_tolerance"]):
                raise ValueError(
                    "Silero probability differs from the bound observation: "
                    f"abs_error={error}"
                )
            self._probabilities.append(actual)
        super().consume(record)

    def finish(self, terminal: str) -> dict[str, Any]:
        if self._pending:
            raise ValueError("Silero replay ended with unmatched model outputs")
        if not self._probabilities:
            raise ValueError("Silero replay produced no checked VAD probabilities")
        return super().finish(terminal)

    def report(self) -> dict[str, Any]:
        payload = np.asarray(self._probabilities, dtype="<f4").tobytes()
        return {
            "binding_sha256": binding_sha256(self._binding),
            "probabilities": len(self._probabilities),
            "probabilities_sha256_f32le": hashlib.sha256(payload).hexdigest(),
            "maximum_abs_error": self._maximum_abs_error,
            "stateful_chunks": len(self._probabilities),
            "latency_claims": False,
        }
