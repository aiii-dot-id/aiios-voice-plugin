"""Exact local-only models for the first live reference. One executor owns MLX."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
from functools import partial
from pathlib import Path

import numpy as np

from .preview_stt import PreviewSTT


class MLXModels:
    def __init__(
        self,
        root: Path,
        *,
        stt_right_context=0,
        semantic_pause=False,
        endpoint_executor=None,
        assets=None,
    ):
        if type(stt_right_context) is not int or stt_right_context not in (0, 3, 6, 13):
            raise ValueError("unsupported STT context")
        import mlx.core as mx
        from mlx_audio.stt import load as load_stt
        from mlx_audio.tts.utils import load_model as load_tts
        from mlx_audio.vad import load as load_vad

        from scripts.verify_snapshot import verify

        self.mx = mx
        # The same pinned control VAD as the native path. Silence/speech
        # decisions must not wait for each TTS decoder step on the MLX worker.
        from .control_vad import ControlVAD

        self.control_vad_factory = (
            partial(ControlVAD, root)
            if assets is None
            else partial(ControlVAD, root, assets=assets)
        )
        self.stt_right_context = stt_right_context
        self.endpoint = None
        self.endpoint_executor = endpoint_executor
        self.identity = {"models": {}, "packages": {}}
        paths = {}
        for key, name in (
            ("stt", "nemotron-3-5-asr-streaming"),
            ("vad", "silero-vad"),
            ("tts", "qwen3-tts"),
        ):
            if assets is not None:
                path = assets.snapshot(key)
                body = assets.manifests[key]
                paths[key] = path
                self.identity["models"][key] = {
                    "repo_id": body["source"]["repo_id"],
                    "revision": body["source"]["revision"],
                    "manifest_sha256": assets.manifest_sha(key),
                    "snapshot": str(path),
                    "verified_files": len(body["files"]),
                }
                continue
            manifest = root / "research" / "acquisition" / f"{name}.json"
            body = json.loads(manifest.read_text())
            digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
            source = body["source"]
            cache = root / "artifacts" / "hf-cache"
            choices = [
                cache / ".contract-views" / digest / "snapshots" / source["revision"],
                cache
                / ("models--" + source["repo_id"].replace("/", "--"))
                / "snapshots"
                / source["revision"],
            ]
            path = next((p for p in choices if p.is_dir()), None)
            if path is None:
                raise RuntimeError(f"exact local artifact missing: {name}")
            result = verify(manifest, path)
            if result.get("status") != "passed":
                raise RuntimeError(
                    f"artifact verification failed: {name}: {result.get('errors')}"
                )
            paths[key] = path
            self.identity["models"][key] = {
                "repo_id": source["repo_id"],
                "revision": source["revision"],
                "manifest_sha256": digest,
                "snapshot": str(path),
                "verified_files": result["verified_files"],
            }
        self.stt = load_stt(paths["stt"], strict=True)
        self.vad = load_vad(paths["vad"], strict=True)
        self.tts = load_tts(paths["tts"], strict=True, lazy=False)
        mx.eval(self.stt.parameters(), self.vad.parameters(), self.tts.parameters())
        for package in ("mlx", "mlx-audio", "numpy", "aiohttp"):
            self.identity["packages"][package] = importlib.metadata.version(package)
        if semantic_pause:
            if endpoint_executor is None:
                raise ValueError("semantic pause requires an independent executor")
            from .semantic_endpoint import SmartTurn

            self.endpoint = (
                SmartTurn(root) if assets is None else SmartTurn(root, assets=assets)
            )
            self.identity["models"]["semantic_endpoint"] = self.endpoint.identity

    def vad_state(self):
        return self.vad.initial_state(sample_rate=16000)

    def vad_feed(self, samples, state):
        probability, state = self.vad.feed(samples, state, sample_rate=16000)
        self.mx.eval(probability, state.state, state.context)
        return float(np.asarray(probability).reshape(-1)[0]), state

    def stt_stream(self):
        return PreviewSTT(self.stt, self.stt_right_context)

    @property
    def stt_preview_policy(self):
        return (
            "confirmation_only"
            if self.stt_right_context == 6
            else "first_nonempty_confirmation_handoff"
        )

    def tts_stream(self, text):
        self.mx.random.seed(17)
        return self.tts.generate(
            text=text,
            temperature=0.9,
            lang_code="English",
            max_tokens=256,
            stream=True,
            streaming_interval=0.32,
            verbose=False,
        )

    @staticmethod
    def tts_next(generator):
        result = next(generator, None)
        if result is None:
            return None
        return (
            np.asarray(result.audio, dtype=np.float32),
            int(result.sample_rate),
            int(result.token_count),
        )
