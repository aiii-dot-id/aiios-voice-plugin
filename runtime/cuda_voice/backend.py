"""Exact existing CUDA checkpoints behind the shared voice-model interface."""

import importlib.metadata
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import numpy as np

from runtime.voice_core.control_vad import ControlVAD
from runtime.voice_core.semantic_endpoint import SmartTurn

from .stt import ResidentSTT


def start_recognizer(command, log):
    # cuda_resident already accepts close after its active stream retires.
    # Preserve ResidentSTT's bounded terminate/kill path for startup or faults.
    return ResidentSTT(command, log, graceful_close=True)


def package_versions(endpoint_identity):
    # CPU and GPU wheels expose the same module under different distribution
    # names. The constructed endpoint already records the runtime it loaded.
    return {
        **{
            p: importlib.metadata.version(p)
            for p in ("torch", "transformers", "numpy", "aiohttp")
        },
        "onnxruntime": endpoint_identity["onnxruntime"],
    }


class CUDAModels:
    max_tokens = 384
    stt_right_context = 6
    stt_preview_policy = "confirmation_only"

    def __init__(
        self,
        root,
        stage,
        output,
        endpoint_executor,
        *,
        record_observations=True,
        runtime_root=None,
        assets=None,
    ):
        import torch
        from qwen_tts import Qwen3TTSModel

        from cuda_tts.cached_streaming import CachedQwen3TTSStreamingAdapter
        from cuda_tts.run_panel import MANIFEST_HASH
        from cuda_tts.run_qwen3_cuda_reference import (
            require_exact_cuda_residency,
            sha256_path,
        )
        from cuda_tts.verify_reference import verify

        self.torch = torch
        self.recognizer = None
        self.synth = None
        self.lock = threading.Lock()
        self.recognition_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="cuda-stt-control"
        )
        if (runtime_root is None) != (assets is None):
            raise ValueError("packaged CUDA requires both runtime and model catalog")
        self.control_vad_factory = partial(
            ControlVAD, root, **({"assets": assets} if assets is not None else {})
        )
        self.endpoint_executor = endpoint_executor
        try:
            torch.set_num_threads(4)
            torch.backends.cuda.matmul.allow_tf32 = torch.backends.cudnn.allow_tf32 = (
                False
            )
            if (
                torch.cuda.get_device_name() != "NVIDIA GeForce RTX 4070 Ti"
                or torch.cuda.mem_get_info()[0] < 7.5 * 1024**3
            ):
                raise RuntimeError("pinned GPU or 7.5 GiB free-memory gate failed")
            torch.cuda.set_per_process_memory_fraction(0.36, 0)
            stt_root, tts_root = (
                stage.parent / "vf100-cuda-stt",
                stage.parent / "vf100-cuda-tts",
            )
            command = [
                str(stage / "stt-python"),
                str(root / "runtime/stt/cuda_resident.py"),
                "--root",
                str(stt_root),
                "--max-input-seconds",
                "60",
            ]
            if runtime_root is not None:
                command = [
                    sys.executable,
                    "-I",
                    "-S",
                    "-B",
                    str(runtime_root / "engine/plugin/runtime_bootstrap.py"),
                    "--stt",
                    "--root",
                    str(root),
                    "--max-input-seconds",
                    "60",
                    "--model-assets",
                    str(assets.catalog),
                    "--model-backend",
                    "cuda",
                ]
            self.recognizer = start_recognizer(
                command,
                output / "stt-worker.log" if record_observations else None,
            )
            if assets is None:
                manifest = tts_root / "cuda_tts/qwen3_tts_reference_manifest.json"
                if sha256_path(manifest) != MANIFEST_HASH:
                    raise ValueError("TTS manifest differs")
                model = json.loads(manifest.read_text())["model"]
                snapshot = (
                    tts_root
                    / "hf-home/hub/models--Qwen--Qwen3-TTS-12Hz-0.6B-Base/snapshots"
                    / model["revision"]
                )
                if not verify(manifest, tts_root / "Qwen3-TTS", snapshot)["ok"]:
                    raise ValueError("TTS snapshot binding failed")
                reference = json.loads(
                    (tts_root / "cuda_tts/reference_fixture.json").read_text()
                )
            else:
                from .installed import tts_inputs

                model, snapshot, reference = tts_inputs(runtime_root, assets)
            from pathlib import Path

            if sha256_path(Path(reference["audio_path"])) != reference["audio_sha256"]:
                raise ValueError("public voice-conditioning fixture differs")
            self.tts = Qwen3TTSModel.from_pretrained(
                str(snapshot),
                device_map="cuda:0",
                dtype=torch.bfloat16,
                attn_implementation="sdpa",
                local_files_only=True,
            )
            self.prompt = self.tts.create_voice_clone_prompt(
                ref_audio=reference["audio_path"],
                ref_text=reference["reference_text"],
                x_vector_only_mode=False,
            )
            self.tts.model.speech_tokenizer.model.to(dtype=torch.float32)
            residency = require_exact_cuda_residency(
                self.tts, expected_codec_dtype=torch.float32
            )
            self.adapter = CachedQwen3TTSStreamingAdapter(self.tts)
            self.endpoint = SmartTurn(
                root, **({"assets": assets} if assets is not None else {})
            )
            self.identity = {
                "models": {
                    "stt": self.recognizer.ready,
                    "tts": {
                        **model,
                        "manifest_sha256": MANIFEST_HASH,
                        "residency": residency,
                    },
                    "semantic_endpoint": self.endpoint.identity,
                    "vad": self.control_vad_factory().identity,
                },
                "packages": package_versions(self.endpoint.identity),
                "backend": "cuda",
                "stt_control": "separate_process_and_executor",
                "tts_reference_sha256": reference["audio_sha256"],
            }
        except BaseException:
            self.close()
            raise

    def stt_stream(self):
        return self.recognizer.stream()

    def tts_stream(self, text):
        self.torch.manual_seed(17)
        self.torch.cuda.manual_seed_all(17)
        run = self.adapter.start_voice_clone(
            text=text,
            language="English",
            voice_clone_prompt=self.prompt,
            frames_per_chunk=6,
            do_sample=True,
            subtalker_dosample=True,
            max_new_tokens=self.max_tokens,
        )
        stream = Synthesis(self, run)
        with self.lock:
            self.synth = stream
        return stream

    @staticmethod
    def tts_next(generator):
        return generator.next()

    def cancel_synthesis(self):
        with self.lock:
            if self.synth is not None:
                self.synth.run.cancel()  # Event/ack only; never waits on inference.

    def close(self):
        self.cancel_synthesis()
        if self.recognizer is not None:
            self.recognizer.close()
        self.recognition_executor.shutdown(wait=True)


class Synthesis:
    def __init__(self, owner, run):
        self.owner, self.run = owner, run
        self.chunks = run.chunks()

    def next(self):
        chunk = next(self.chunks, None)
        if chunk is None:
            r = self.run.result
            if r.cancelled:
                return None  # The shared service already fenced this cancelled job.
            if (
                r.terminal_reason != "natural_eos"
                or r.streamed_vs_canonical_length_delta_samples != 0
                or r.parity_max_abs is None
                or r.parity_max_abs > 0.002
                or r.parity_rms > 0.0002
            ):
                raise RuntimeError("TTS did not naturally finish with codec parity")
            return None
        if len(chunk.samples) % 1920:
            raise RuntimeError("partial codec frame")
        return (
            np.asarray(chunk.samples, dtype=np.float32),
            chunk.sample_rate,
            len(chunk.samples) // 1920,
        )

    def close(self):
        self.run.cancel()
        self.chunks.close()
        with self.owner.lock:
            if self.owner.synth is self:
                self.owner.synth = None
