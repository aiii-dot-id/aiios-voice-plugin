"""Pinned local Qwen3-TTS on native MLX GPU, with no implicit downloads."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np


class MLXTTSBackend:
    max_tokens = 256

    def __init__(self, root):
        import mlx.core as mx
        from mlx_audio.tts.utils import load_model

        self.mx = mx
        if not mx.metal.is_available() or mx.default_device() != mx.gpu:
            raise RuntimeError("native MLX GPU required; no CPU fallback")
        root = Path(root)
        manifest = root / "research/acquisition/qwen3-tts.json"
        data = json.loads(manifest.read_text())
        digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
        source = data["source"]
        cache = root / "artifacts/hf-cache"
        choices = [
            cache / ".contract-views" / digest / "snapshots" / source["revision"],
            cache
            / ("models--" + source["repo_id"].replace("/", "--"))
            / "snapshots"
            / source["revision"],
        ]
        snapshot = next((p for p in choices if p.is_dir()), None)
        if snapshot is None:
            raise RuntimeError("complete verified local TTS snapshot required")
        # Reuse the existing verifier's supported CLI, without changing its
        # script-import environment or importing research modules into the host.
        checked = subprocess.run(
            [
                sys.executable,
                str(root / "scripts/verify_snapshot.py"),
                str(manifest),
                str(snapshot),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if checked.returncode or json.loads(checked.stdout).get("status") != "passed":
            raise RuntimeError("complete verified local TTS snapshot required")
        self.model = load_model(snapshot, strict=True, lazy=False)
        mx.eval(self.model.parameters())
        self.identity = {
            "repo_id": source["repo_id"],
            "revision": source["revision"],
            "manifest_sha256": digest,
            "backend": "mlx-metal",
            "sample_rate": 24000,
            "max_tokens_per_segment": self.max_tokens,
        }

    def tts_stream(self, text):
        self.mx.random.seed(getattr(self, "tts_seed", 17))
        # A validated immutable session view supplies options. Standalone and
        # already frozen checkpoint callers retain their exact prior defaults.
        options = dict(
            getattr(
                self,
                "tts_generation_options",
                {
                    "temperature": 0.9,
                    "lang_code": "English",
                },
            )
        )
        return self.model.generate(
            text=text,
            **options,
            max_tokens=self.max_tokens,
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
