"""GTX 1070 adapter: isolated dependency lanes, explicit precision, no offload."""

import importlib.metadata
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from runtime.cuda_voice.backend import CUDAModels
from runtime.cuda_voice.stt import ResidentSTT
from runtime.voice_core.control_vad import ControlVAD
from runtime.voice_core.semantic_endpoint import SmartTurn

from .binding import residency, verify_tts
from .precision import predictor_fp32
from .predictor_graph import PredictorGraph


class WindowsModels(CUDAModels):
    # A physical C920 soft onset exceeded the generic 256 ms lookback. Retain
    # bounded prior audio; never delay the VAD/control lane to collect it.
    stt_preroll_frames = 32

    def __init__(
        self,
        root,
        stage,
        output,
        endpoint_executor,
        export_sha,
        *,
        recognizer=True,
        predictor_graph=False,
    ):
        import torch
        from qwen_tts import Qwen3TTSModel

        from cuda_tts.cached_streaming import CachedQwen3TTSStreamingAdapter

        if sys.platform != "win32":
            raise RuntimeError("This qualification adapter requires Windows")
        self.torch, self.recognizer, self.synth = torch, None, None
        self.lock = threading.Lock()
        self.recognition_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="windows-stt"
        )
        self.endpoint_executor = endpoint_executor
        self.control_vad_factory = partial(ControlVAD, root)
        try:
            torch.set_num_threads(4)
            torch.backends.cuda.matmul.allow_tf32 = torch.backends.cudnn.allow_tf32 = (
                False
            )
            if torch.cuda.get_device_name() != "NVIDIA GeForce GTX 1070":
                raise RuntimeError("Windows target GPU differs")
            if torch.cuda.mem_get_info()[0] < 5 * 1024**3:
                raise RuntimeError(
                    "Windows requires 5 GiB free before co-resident load"
                )
            torch.cuda.set_per_process_memory_fraction(
                3584 * 1024**2 / torch.cuda.get_device_properties(0).total_memory
            )
            snapshot, audio, reference, identity = verify_tts(stage, export_sha)
            if recognizer:
                env = {
                    k: v
                    for k, v in os.environ.items()
                    if k not in {"PYTHONPATH", "PYTHONHOME"}
                }
                # Invoke the actual interpreter, not Windows' venv redirector.
                # Terminating that redirector leaves its Python child/pipe alive.
                env["PYTHONPATH"] = str(stage / "runtime/Lib/site-packages")
                self.recognizer = ResidentSTT(
                    [
                        sys._base_executable,
                        "-u",
                        str(root / "runtime/stt/cuda_resident.py"),
                        "--root",
                        str(stage / "stt"),
                        "--max-input-seconds",
                        "60",
                        "--memory-budget-mib",
                        "3072",
                    ],
                    output / "stt-worker.log",
                    env=env,
                )
            self.tts = Qwen3TTSModel.from_pretrained(
                str(snapshot),
                device_map="cuda:0",
                dtype=torch.float16,
                attn_implementation="sdpa",
                local_files_only=True,
            )
            # Convert before conditioning as well as decoding: Pascal has no native BF16.
            self.tts.model.speech_tokenizer.model.to(dtype=torch.float32)
            self.precision_handle, precision = predictor_fp32(self.tts, torch)
            self.prompt = self.tts.create_voice_clone_prompt(
                ref_audio=str(audio),
                ref_text=reference["reference_text"],
                x_vector_only_mode=False,
            )
            identity["residency"] = residency(
                self.tts, torch.float16, torch.float32, fp32_predictor=True
            )
            identity["precision_policy"] = precision
            torch.cuda.empty_cache()  # Release loader/conditioning scratch, not resident parameters.
            self.predictor_graph = None
            if predictor_graph:
                # Preparation happens before readiness/admission, never in the
                # interruption lane or first user synthesis. Weights are unchanged.
                self.predictor_graph = PredictorGraph(
                    self.tts.model.talker.code_predictor, torch
                )
                with torch.inference_mode():
                    self.predictor_graph.prepare(
                        torch.zeros((1, 2, 1024), dtype=torch.float16, device="cuda:0")
                    )
                self.tts.model.talker.code_predictor.generate = self.predictor_graph
            identity["predictor_execution"] = {
                "mode": "cuda_graph_fixed_15"
                if predictor_graph
                else "original_generate",
                "sampling_changed": False,
                "prepared_before_admission": predictor_graph,
            }
            identity["memory_after_conditioning"] = {
                "allocated": torch.cuda.memory_allocated(),
                "reserved": torch.cuda.memory_reserved(),
                "device_free": torch.cuda.mem_get_info()[0],
            }
            self.adapter = CachedQwen3TTSStreamingAdapter(self.tts)
            self.endpoint = SmartTurn(root)
            self.identity = {
                "backend": "windows-cuda",
                "target_gpu": torch.cuda.get_device_name(),
                "models": {
                    "tts": identity,
                    "stt": self.recognizer.ready if self.recognizer else None,
                    "semantic_endpoint": self.endpoint.identity,
                    "vad": self.control_vad_factory().identity,
                },
                "tts_reference_sha256": reference["audio_sha256"],
                "packages": {
                    p: importlib.metadata.version(p)
                    for p in ("torch", "transformers", "onnxruntime-directml")
                },
                "stt_control": "separate_process_environment_and_executor",
                "stt_preroll_frames": self.stt_preroll_frames,
                "tts_allocator_budget_mib": 3584,
                "minimum_device_free_mib_at_synthesis_admission": 128,
            }
        except BaseException:
            self.close()
            raise

    def tts_stream(self, text):
        # The allocator limit is a ceiling, not a reservation. Refuse admission
        # when unrelated desktop activity has consumed the measured reserve.
        # Allocation failures still propagate; there is no CPU/precision fallback.
        if self.torch.cuda.mem_get_info()[0] < 128 * 1024**2:
            raise RuntimeError("Windows TTS device-memory reserve unavailable")
        return super().tts_stream(text)
