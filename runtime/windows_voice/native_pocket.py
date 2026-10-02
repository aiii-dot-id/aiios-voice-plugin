"""Direct composition: resident Vulkan TTS, existing DirectML STT and pause/VAD.

No initial Torch TTS model, external resource override, or writable model view.
The optional exact native endpoint removes the Python Torch frontend, only
when selected and bound by the runtime profile. Existing profiles are unchanged.
"""

import builtins
import importlib.metadata
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from runtime.cuda_voice.stt import ResidentSTT
from runtime.native_endpoint.profile import selection as native_endpoint_selection
from runtime.native_pocket.backend import ASSETS, NativePocketBackend
from runtime.native_pocket.profile import selection
from runtime.physical_paths import path_identity
from runtime.stt.profile import native_catalog
from runtime.voice_core.control_vad import ControlVAD
from runtime.voice_core.endpoint_profile import selection as endpoint_selection
from runtime.voice_core.semantic_endpoint import SmartTurn


class WindowsNativePocketModels:
    max_tokens = 750
    stt_preroll_frames = 32
    stt_right_context = 6
    stt_preview_policy = "confirmation_only"

    def __init__(self, root, endpoint_executor, *, runtime_root, assets):
        config = selection(runtime_root)
        frontend = endpoint_selection(runtime_root)
        native_endpoint = native_endpoint_selection(runtime_root)
        if sys.platform != "win32" or config is None or assets is None:
            raise ValueError("explicit bound Windows native runtime and models required")
        if path_identity(assets.catalog) != path_identity(native_catalog(runtime_root)):
            raise ValueError("native model catalog differs from runtime selection")
        self.adapter = self.recognizer = self.endpoint = None
        self.recognition_executor = ThreadPoolExecutor(1, thread_name_prefix="windows-native-stt-control")
        self.endpoint_executor = endpoint_executor
        self.control_vad_factory = partial(ControlVAD, root, assets=assets)
        start, phases = time.perf_counter(), {}
        try:
            model_root = assets.snapshot("tts")
            phases["tts_verification"] = time.perf_counter()-start
            env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON")}
            env["HF_HUB_OFFLINE"] = "1"
            self.recognizer = ResidentSTT(
                [sys.executable, "-I", "-S", "-B", str(runtime_root / "engine/plugin/runtime_bootstrap.py"), "--stt"],
                None, env=env, wait_ready=False, separate_stderr=True, graceful_close=True,
            )
            phase = time.perf_counter()
            # Runtime selection, not an ambient automatic backend preference.
            os.environ.update(GGML_VK_DISABLE_F16="1", GGML_VK_VISIBLE_DEVICES="0")
            self.adapter = NativePocketBackend(
                config["library"], model_root, config_path=config["config"],
                binary_sha256=config["library_sha256"], backend="vulkan",
                threads=config["threads"], seed=config["seed"], max_steps=config["max_steps"],
            )
            phases["tts_native_load"] = time.perf_counter()-phase
            tts_ready = time.perf_counter()-start
            phase = time.perf_counter()
            if native_endpoint is not None:
                from runtime.native_endpoint.backend import NativeEndpoint

                self.endpoint = NativeEndpoint(native_endpoint, assets)
            else:
                self.endpoint = SmartTurn(root, assets=assets, frontend=frontend)
            phases["semantic_endpoint_startup"] = time.perf_counter()-phase
            phases["semantic_endpoint_phases"] = self.endpoint.startup_seconds
            phase = time.perf_counter()
            self.recognizer.await_ready()
            if self.recognizer.ready.get("backend") != "native-directml":
                raise RuntimeError("native recognizer selection was not honored")
            phases["recognizer_readiness_wait"] = time.perf_counter()-phase
            self.identity = {
                "backend": "windows-pocket-vulkan-stt-directml",
                "models": {"tts": self.adapter.identity, "stt": self.recognizer.ready,
                           "semantic_endpoint": self.endpoint.identity, "vad": self.control_vad_factory().identity},
                "tts_reference_sha256": ASSETS["embeddings/alba.safetensors"],
                "packages": {name: importlib.metadata.version(name) for name in (
                    ("numpy", "onnxruntime-directml") if native_endpoint is not None else
                    ("numpy", "torch", "onnxruntime-directml") if frontend is not None
                    else ("numpy", "torch", "transformers", "onnxruntime-directml"))},
                "stt_control": "separate_process_environment_and_executor",
                "startup_schedule": "native_stt_process_overlaps_native_tts_and_existing_endpoint",
                "stt_preroll_frames": self.stt_preroll_frames,
                "startup_seconds": {"tts_bound_loaded": tts_ready, "models_ready": time.perf_counter()-start, "phases": phases},
                "torch_scope": ("native ATen libraries only; no Python Torch" if native_endpoint is not None
                                else "retained verified SmartTurn frontend; no Torch TTS model"),
            }
        except BaseException as original:
            try:
                self.close()
            except Exception as retirement:  # noqa: BLE001 - report both failures, never hide retirement.
                raise builtins.BaseExceptionGroup("native model load and retirement failed", [original, retirement]) from None
            raise

    def stt_stream(self):
        return self.recognizer.stream()

    def tts_stream(self, text):
        return self.adapter.tts_stream(text)

    @staticmethod
    def tts_next(stream):
        return stream.next()

    def cancel_synthesis(self):
        if self.adapter is not None:
            self.adapter.cancel_synthesis()

    def close(self):
        failures = []
        for close in (self.cancel_synthesis,
                      self.recognizer.close if self.recognizer is not None else lambda: None,
                      self.recognition_executor.shutdown,
                      self.endpoint.close if hasattr(self.endpoint, "close") else lambda: None,
                      self.adapter.close if self.adapter is not None else lambda: None):
            try:
                close()
            except Exception as error:  # noqa: BLE001 - attempt every owned cleanup and report all failures.
                failures.append(error)
        if failures:
            raise builtins.ExceptionGroup("native models did not retire cleanly", failures)
