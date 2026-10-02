"""Opt-in whole-utterance UID using the original weights on desktop GPUs.

The embedding binding differs from the fixed-window recognizer. Enrollments
must be created explicitly under this policy; no existing store is migrated.
Execution receipt/layout is separate from the shared numerical space.
"""

import hashlib
import importlib.metadata
import threading
from pathlib import Path

from .backend import (
    FRONTEND,
    MODEL_SHA256,
    Embedding,
    fbank_from_samples,
    file_sha256,
    read_pcm16,
    read_pcm16_wav,
)
from .identity import SpeakerIdentityError, digest, unit

FULL_FRONTEND = {
    **{
        k: v
        for k, v in FRONTEND.items()
        if k not in {"window_frames", "tail", "aggregation"}
    },
    "temporal_context": "one_unpadded_whole_utterance",
    "pooling": "original_model_attentive_statistics_pooling",
    "aggregation": "unit_single_full_context_embedding",
}


class FullContextSpeaker:
    def __init__(
        self,
        source: Path,
        *,
        provider: str,
        lowering: Path | None = None,
        lowering_manifest_sha256: str | None = None,
    ):
        if provider not in {"mlx", "cuda", "directml"}:
            raise SpeakerIdentityError("explicit full-context GPU backend required")
        if file_sha256(source) != MODEL_SHA256:
            raise SpeakerIdentityError("full-context source model differs")
        self.binding_record = {
            "source_sha256": MODEL_SHA256,
            "model_sha256": MODEL_SHA256,
            "frontend": FULL_FRONTEND,
            "kaldi_native_fbank": importlib.metadata.version("kaldi-native-fbank"),
        }
        self.binding = digest(self.binding_record)
        self.lock = threading.Lock()
        self.runtime = {"requested_provider": provider, "gpu_execution_proven": False}
        if provider == "mlx":
            from .mlx_graph import MLXSpeakerGraph

            if lowering is None or lowering_manifest_sha256 is None:
                raise SpeakerIdentityError("exact MLX lowering and manifest required")
            self.model = MLXSpeakerGraph(lowering, lowering_manifest_sha256)
            self.runtime.update(
                lowered_manifest_sha256=lowering_manifest_sha256,
                mlx=importlib.metadata.version("mlx"),
            )
            self.infer = self.model
        else:
            if lowering is not None or lowering_manifest_sha256 is not None:
                raise SpeakerIdentityError("MLX lowering is not an ONNX model")
            from runtime.onnx_runtime import local_runtime

            ort = local_runtime()
            requested = {
                "cuda": "CUDAExecutionProvider",
                "directml": "DmlExecutionProvider",
            }[provider]
            if requested not in ort.get_available_providers():
                raise SpeakerIdentityError("requested full-context GPU unavailable")
            options = ort.SessionOptions()
            options.intra_op_num_threads, options.inter_op_num_threads = 2, 1
            provider_options = {}
            if provider == "cuda":
                if hasattr(ort, "preload_dlls"):
                    ort.preload_dlls(directory="")
                provider_options = {
                    "use_tf32": "0",
                    "gpu_mem_limit": str(1024**3),
                    "cudnn_conv_algo_search": "HEURISTIC",
                    "cudnn_conv_use_max_workspace": "0",
                }
            else:
                options.enable_mem_pattern = False
                options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            self.model = ort.InferenceSession(
                str(source),
                sess_options=options,
                providers=[(requested, provider_options)],
            )
            self.model.disable_fallback()
            if self.model.get_providers()[0] != requested or [
                (i.name, i.shape) for i in self.model.get_inputs()
            ] != [("feats", ["B", "T", 80])]:
                raise SpeakerIdentityError(
                    "full-context runtime/provider shape differs"
                )
            self.runtime.update(onnxruntime=ort.__version__, options=provider_options)
            self.infer = lambda features: self.model.run(["embs"], {"feats": features})[
                0
            ]

    def embed(self, path: Path) -> Embedding:
        return self._embed_samples(*read_pcm16(path))

    def embed_wav(self, audio: bytes) -> Embedding:
        return self._embed_samples(*read_pcm16_wav(audio))

    def _embed_samples(self, samples, rate) -> Embedding:
        features = fbank_from_samples(samples, rate)
        audio_sha = hashlib.sha256(samples.astype("<i2").tobytes()).hexdigest()
        with self.lock:
            vector = unit(self.infer(features)[0])
        vector.setflags(write=False)
        return Embedding(vector, self.binding, audio_sha, len(samples) / rate, 1)
