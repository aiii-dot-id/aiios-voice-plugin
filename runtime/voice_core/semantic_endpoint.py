"""Audio-only completion inference, never an interruption or capture authority."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import inspect
import json
import time
from collections import deque
from pathlib import Path

import numpy as np

WINDOW = 128000


def pause_samples(milliseconds):
    """Round up to an input block; a configured pause must never end early."""
    if type(milliseconds) is not int or not 320 <= milliseconds <= 5000:
        raise ValueError("turn_pause_ms must be a whole number from 320 to 5000")
    return ((milliseconds * 16 + 511) // 512) * 512


class PauseGate:
    """Audio-clock provisional window, owned by the sequential input consumer.

    Query at 640 ms, decide at 768 ms, never when a worker happens to finish.
    Speech through that horizon wins; later captured speech cannot rewrite it.
    At the horizon only the model-audio owner waits (bounded). Capture and the
    interruption lane remain independent. No result can stop playback.
    """

    trigger_samples = 10240
    commitment_samples = 12288
    maximum_silence_samples = 30720
    hold_threshold = 0.01
    query_timeout_seconds = 0.25

    @classmethod
    def policy(cls):
        return {
            "clock": "ordered_audio_samples",
            "sample_rate": 16000,
            "query_samples": cls.trigger_samples,
            "commitment_samples": cls.commitment_samples,
            "maximum_silence_samples": cls.maximum_silence_samples,
            "hold_threshold": cls.hold_threshold,
            "query_timeout_seconds": cls.query_timeout_seconds,
        }

    def __init__(self, predict, executor, record):
        self.predict, self.executor, self.record = predict, executor, record
        self.audio = deque(maxlen=250)  # 8 seconds of exact 512-sample blocks
        self.epoch = 0
        self.pending = None
        self.retired = []
        self.checked = False
        self.position = 0

    def reset(self):
        self._retire()
        self.epoch += 1
        self.audio.clear()
        self.checked = False

    def configure_pause(self, milliseconds):
        """Pin before input. Preserve the 128ms provisional and 1152ms hold spans."""
        if self.position or self.audio or self.pending is not None or self.retired:
            raise ValueError("pause settings are pinned before session input")
        commitment = pause_samples(milliseconds)
        self.trigger_samples = commitment - 2048
        self.commitment_samples = commitment
        self.maximum_silence_samples = commitment + 18432
        return {
            **self.policy(),
            "requested_pause_ms": milliseconds,
            "query_samples": self.trigger_samples,
            "commitment_samples": self.commitment_samples,
            "maximum_silence_samples": self.maximum_silence_samples,
        }

    def append(self, samples):
        if len(samples) % 512:
            raise ValueError("pause audio must use exact model blocks")
        for start in range(0, len(samples), 512):
            self.audio.append(
                np.array(samples[start : start + 512], dtype=np.float32, copy=True)
            )

    def _retire(self):
        if self.pending is not None:
            self.retired.append((self.pending, self.position))
            self.pending = None

    async def _resolve(self, query, *, stale, position):
        future, _epoch, boundary, started = query
        # Shield the executor future: cancellation does not stop its thread.
        # Keep ownership until close(), which observes any outstanding result.
        try:
            if not future.done():
                await asyncio.wait_for(
                    asyncio.shield(future),
                    max(0, self.query_timeout_seconds - (time.monotonic() - started)),
                )
        except TimeoutError as error:
            raise TimeoutError(
                "semantic endpoint exceeded 250ms; no turn committed"
            ) from error
        probability = float(future.result())
        if not np.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("invalid semantic completion probability")
        self.record(
            {
                "type": "decision",
                "position": boundary,
                "resolution_position": position,
                "probability": probability,
                "stale": stale,
                "elapsed_ms": (time.monotonic() - started) * 1000,
            }
        )
        return probability

    async def _reap(self, *, wait=False):
        while self.retired:
            query, position = self.retired[0]
            if not wait and not query[0].done():
                return
            await self._resolve(query, stale=True, position=position)
            self.retired.pop(0)

    async def poll(self, *, speech, silence, position):
        # position is consumed model audio, not a native-arrival high watermark.
        self.position = position
        if speech:
            self._retire()
            self.epoch += 1
            self.checked = False
        await self._reap()
        if speech:
            return None
        if silence >= self.maximum_silence_samples:
            return "bounded_silence_fallback"
        if (
            silence >= self.trigger_samples
            and not self.checked
            and self.pending is None
        ):
            # A rapid replay may reach a new pause before a stale worker exits.
            # Join it at this audio boundary, rather than moving the new query.
            await self._reap(wait=True)
            if not self.audio:
                raise ValueError("cannot classify empty turn")
            samples = np.concatenate(self.audio)
            self.record(
                {
                    "type": "query",
                    "position": position,
                    "samples": len(samples),
                    "audio_sha256": hashlib.sha256(
                        samples.astype("<f4").tobytes()
                    ).hexdigest(),
                }
            )
            self.pending = (
                asyncio.get_running_loop().run_in_executor(
                    self.executor, self.predict, samples
                ),
                self.epoch,
                position,
                time.monotonic(),
            )
        if self.pending is not None and silence >= self.commitment_samples:
            probability = await self._resolve(
                self.pending, stale=False, position=position
            )
            self.pending = None
            self.checked = True
            if probability > self.hold_threshold:
                return "semantic_no_hold"
        return None

    async def close(self):
        self._retire()
        await self._reap(wait=True)


class SmartTurn:
    """Exact admitted ONNX checkpoint with upstream-compatible Whisper features."""

    def __init__(self, root: Path, *, backend="cpu", assets=None, frontend=None):
        begun = time.perf_counter()
        phases = {}
        from runtime.onnx_runtime import local_runtime

        ort = local_runtime()
        phases["onnx_import"] = time.perf_counter() - begun
        phase = time.perf_counter()
        import torch

        if frontend is None:
            from transformers import WhisperFeatureExtractor
        else:
            from runtime.voice_core.whisper_torch_frontend import TorchWhisperFrontend

        from scripts.verify_snapshot import verify

        phases["frontend_imports"] = time.perf_counter() - phase
        phase = time.perf_counter()
        if assets is None:
            manifest = root / "research/acquisition/smart-turn-v3.2.json"
            digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
            body = json.loads(manifest.read_text())
            snapshot = (
                root
                / "artifacts/hf-cache/.contract-views"
                / digest
                / "snapshots"
                / body["source"]["revision"]
            )
            verification = verify(manifest, snapshot)
            if verification["status"] != "passed":
                raise ValueError("semantic endpoint artifact verification failed")
        else:
            snapshot = assets.snapshot("endpoint")
            digest = assets.manifest_sha("endpoint")
            body = assets.manifests["endpoint"]
        phases["artifact_verification"] = time.perf_counter() - phase
        phase = time.perf_counter()
        if backend not in ("cpu", "cpu-fp32", "coreml"):
            raise ValueError("unknown semantic endpoint backend")
        filename = (
            "smart-turn-v3.2-cpu.onnx"
            if backend == "cpu"
            else "smart-turn-v3.2-gpu.onnx"
        )
        providers = ["CPUExecutionProvider"]
        if backend == "coreml":
            if "CoreMLExecutionProvider" not in ort.get_available_providers():
                raise ValueError("CoreML endpoint provider unavailable")
            providers.insert(
                0, ("CoreMLExecutionProvider", {"MLComputeUnits": "CPUAndGPU"})
            )
        options = ort.SessionOptions()
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(snapshot / filename), options, providers=providers
        )
        phases["onnx_session"] = time.perf_counter() - phase
        phase = time.perf_counter()
        self.bound_frontend = None
        if frontend is None:
            self.extractor = WhisperFeatureExtractor(chunk_length=8)
            frontend_identity = {
                "frontend": "upstream WhisperFeatureExtractor torch-cpu",
                "transformers": importlib.metadata.version("transformers"),
                "frontend_source_sha256": hashlib.sha256(
                    Path(inspect.getfile(WhisperFeatureExtractor)).read_bytes()
                ).hexdigest(),
            }
        else:
            self.bound_frontend = TorchWhisperFrontend(
                frontend["coefficients"], frontend["sha256"]
            )
            frontend_identity = dict(self.bound_frontend.identity)
        # This standalone process uses MLX for STT/TTS. Bound the small CPU
        # frontend rather than letting Torch create a competing thread pool.
        torch.set_num_threads(1)
        phases["frontend_construction"] = time.perf_counter() - phase
        self.startup_seconds = {"total": time.perf_counter() - begun, **phases}
        self.identity = {
            "repo_id": body["source"]["repo_id"],
            "revision": body["source"]["revision"],
            "manifest_sha256": digest,
            "file": filename,
            "backend": backend,
            "providers": self.session.get_providers(),
            "onnxruntime": ort.__version__,
            "torch": torch.__version__,
            "torch_threads": 1,
            **frontend_identity,
        }

    def features(self, audio):
        if self.bound_frontend is not None:
            return self.bound_frontend.features(audio)
        samples = np.asarray(audio, dtype=np.float32)
        if samples.ndim != 1 or not len(samples) or not np.isfinite(samples).all():
            raise ValueError("semantic endpoint requires finite nonempty mono audio")
        samples = samples[-WINDOW:]
        samples = np.pad(samples, (WINDOW - len(samples), 0))
        # Upstream normalizes after LEFT padding, not before. Use its actual
        # frontend: tiny FFT differences changed the INT8 decision probability
        # in our attempted NumPy substitution, so that substitution is refused.
        return self.extractor(
            samples,
            sampling_rate=16000,
            return_tensors="np",
            padding="max_length",
            max_length=WINDOW,
            truncation=True,
            do_normalize=True,
            device="cpu",
        ).input_features.astype(np.float32)

    def probability(self, audio):
        output = self.session.run(None, {"input_features": self.features(audio)})
        probability = float(np.asarray(output[0]).reshape(-1)[0])
        if not np.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("invalid semantic completion probability")
        return probability
