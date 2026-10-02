"""Explicit, source-bound preset TTS on CPU with the existing CUDA recognizer.

CPU is the measured faster Pocket execution path on Pascal, not a silent
fallback. The Qwen/voice-conditioning path remains separately selectable.
"""

import hashlib
import importlib.metadata
import json
import os
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from runtime.physical_paths import path_identity

from runtime.cuda_voice.backend import CUDAModels
from runtime.cuda_voice.stt import ResidentSTT
from runtime.speech_output.pocket_backend import PocketBackend
from runtime.speech_output.pocket_loading import load_bound_model
from runtime.stt.profile import native_catalog
from runtime.voice_core.control_vad import ControlVAD
from runtime.voice_core.endpoint_profile import selection as endpoint_selection
from runtime.voice_core.semantic_endpoint import SmartTurn
from runtime.windows_voice.pocket_execution import apply as apply_execution
from runtime.windows_voice.pocket_execution import selection

REVISION = "896e934690afc0e1047a3667a13514386c1420fc"
ARCHIVE_SHA = "7ecb0474e7af040402ec9612a43b3b7ab9bfbd78db1726f9826142094070f8b6"
MODEL_REVISION = "d29db7978e464fb90cb3359ee0c69a273b9142cc"
VOICE_REVISION = "e81d79e8194ad4c7ce879c87a4258ef20cbf2487"
ASSETS = (
    (
        "model.safetensors",
        MODEL_REVISION,
        219029196,
        "be9c6b4876d3f30740a8225dfcaa2e43dc4aeb753c15272735bee16bbb4abb0a",
    ),
    (
        "tokenizer.model",
        MODEL_REVISION,
        59339,
        "d461765ae179566678c93091c5fa6f2984c31bbe990bf1aa62d92c64d91bc3f6",
    ),
    (
        "embeddings/alba.safetensors",
        VOICE_REVISION,
        6194424,
        "69c32db63ca56843d994f81f343f62e0bf2d73f7e4c9bc73e44bb1110b1d8845",
    ),
)


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def verify_pocket(work, *, code_root=None, model_root=None):
    """Verify source and all inference assets before importing/loading them."""
    code_root = code_root or work
    archive = code_root.parent / "pocket-source-20260908.zip"
    if digest(archive) != ARCHIVE_SHA:
        raise ValueError("Pocket source archive changed")
    source = code_root / "source"
    with zipfile.ZipFile(archive) as package:
        expected = {
            r.filename: hashlib.sha256(package.read(r)).hexdigest()
            for r in package.infolist()
            if not r.is_dir()
        }
    actual = {
        p.relative_to(source).as_posix(): digest(p)
        for p in source.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    if actual != expected:
        raise ValueError("Pocket source file set or bytes changed")
    assets = []
    for name, revision, size, sha in ASSETS:
        path = (model_root if model_root is not None else work / "models") / name
        if path.stat().st_size != size or digest(path) != sha:
            raise ValueError(f"Pinned Pocket asset differs: {name}")
        assets.append({"path": name, "revision": revision, "size": size, "sha256": sha})
    return {
        "code_revision": REVISION,
        "archive_sha256": ARCHIVE_SHA,
        "model_repository": "kyutai/pocket-tts-without-voice-cloning",
        "assets": assets,
        "voice_cloning": False,
        "preset": "alba",
    }


class WindowsPocketModels(CUDAModels):
    stt_preroll_frames = 32
    max_tokens = PocketBackend.max_tokens

    def __init__(
        self,
        root,
        stage,
        output,
        endpoint_executor,
        *,
        pocket_root,
        record_observations=True,
        runtime_root=None,
        assets=None,
        native_stt_root=None,
        native_stt_python=None,
    ):
        if sys.platform != "win32":
            raise RuntimeError("This qualification adapter requires Windows")
        if assets is not None and runtime_root is None:
            raise ValueError(
                "installed model assets require the packaged Windows runtime"
            )
        private_native = native_stt_root is not None or native_stt_python is not None
        if private_native and (native_stt_root is None or native_stt_python is None):
            raise ValueError(
                "Native candidate requires both its explicit root and interpreter"
            )
        if private_native and (runtime_root is not None or assets is not None):
            raise ValueError(
                "Private native candidate cannot replace a sealed installed runtime"
            )
        installed_native = (
            native_catalog(runtime_root) if runtime_root is not None else None
        )
        if installed_native is not None and (
            assets is None or path_identity(assets.catalog) != path_identity(installed_native)
        ):
            raise ValueError(
                "native STT model catalog differs from its runtime profile"
            )
        native_stt = private_native or installed_native is not None
        selected_execution = selection(runtime_root)
        self.recognizer = self.adapter = None
        self.recognition_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="windows-pocket-stt"
        )
        self.endpoint_executor = endpoint_executor
        self.control_vad_factory = (
            partial(ControlVAD, root)
            if assets is None
            else partial(ControlVAD, root, assets=assets)
        )
        begun = time.perf_counter()
        startup = {}
        try:
            code_root = runtime_root / "vendor/pocket" if runtime_root else pocket_root
            model_root = (
                assets.snapshot("tts") if assets is not None else pocket_root / "models"
            )
            identity = verify_pocket(
                pocket_root,
                code_root=code_root,
                **({"model_root": model_root} if assets is not None else {}),
            )
            startup["tts_verification"] = time.perf_counter() - begun
            phase_begin = time.perf_counter()
            # The child owns STT verification, CUDA budget admission and loading.
            # Launch it before independent CPU TTS imports; join readiness only
            # after the other models load. Keep the owned handle throughout so
            # any parent-side failure reaps the still-loading child.
            env = {
                k: v
                for k, v in os.environ.items()
                if k not in {"PYTHONPATH", "PYTHONHOME"}
            }
            if runtime_root is None:
                env["PYTHONPATH"] = str(stage / "runtime/Lib/site-packages")
            # Preserve the previous child's offline contract even though its
            # launch now precedes the parent's TTS import/environment setup.
            env["HF_HUB_OFFLINE"] = "1"
            stt_command = [
                sys._base_executable,
                "-X",
                "utf8",
                "-u",
                str(root / "runtime/stt/cuda_resident.py"),
                "--root",
                str(stage / "stt"),
                "--max-input-seconds",
                "60",
                "--memory-budget-mib",
                "3072",
            ]
            if runtime_root is not None:
                stt_command = [
                    sys.executable,
                    "-I",
                    "-S",
                    "-B",
                    str(runtime_root / "engine/plugin/runtime_bootstrap.py"),
                    "--stt",
                    "--root",
                    str(stage if assets is not None else stage / "stt"),
                    "--max-input-seconds",
                    "60",
                    "--memory-budget-mib",
                    "3072",
                ]
                if assets is not None:
                    stt_command += ["--model-assets", str(assets.catalog)]
            if installed_native is not None:
                # The child independently obtains backend and catalog from the
                # same verified runtime. No interpreter/model override is sent.
                stt_command = stt_command[:6]
            if private_native:
                # Private qualification only: keep dependency-heavy CPU TTS in
                # this process and the native recognizer in its clean interpreter.
                # Its child verifies the frozen candidate before any model load.
                env["PYTHONPATH"] = str(root)
                env["PYTHONNOUSERSITE"] = "1"
                env["PYTHONDONTWRITEBYTECODE"] = "1"
                stt_command = [
                    str(native_stt_python),
                    "-X",
                    "utf8",
                    "-B",
                    "-m",
                    "runtime.stt.native_resident",
                    "--native-root",
                    str(native_stt_root),
                ]
            self.recognizer = ResidentSTT(
                stt_command,
                output / "stt-worker.log" if record_observations else None,
                env=env,
                wait_ready=False,
                **(
                    {"separate_stderr": True, "graceful_close": True}
                    if native_stt
                    else {}
                ),
            )
            startup["recognizer_launch"] = time.perf_counter() - phase_begin
            phase_begin = time.perf_counter()
            if runtime_root is None:
                sys.path[:0] = [str(pocket_root / "source"), str(pocket_root / "deps")]
            os.environ["HF_HUB_OFFLINE"] = "1"
            import torch
            import yaml

            startup["tts_imports"] = time.perf_counter() - phase_begin
            phase_begin = time.perf_counter()

            # Match the proven component, including its thread count/seed.
            torch.set_num_threads(1)
            self.torch = torch
            if (
                not native_stt
                and torch.cuda.get_device_name() != "NVIDIA GeForce GTX 1070"
            ):
                raise RuntimeError("Windows qualification GPU differs")
            # The STT child checks its 3072 MiB cap plus reserve BEFORE allocating.
            # A parent check here would subtract the child's own concurrent load
            # and incorrectly require that same reservation a second time.
            config = yaml.safe_load(
                (
                    code_root / "source/pocket_tts/config/english_2026-04.yaml"
                ).read_text()
            )
            config["weights_path"] = str(model_root / "model.safetensors")
            config["weights_path_without_voice_cloning"] = None
            config["flow_lm"]["lookup_table"]["tokenizer_path"] = str(
                model_root / "tokenizer.model"
            )
            config_bytes = yaml.safe_dump(config).encode("utf8")
            if record_observations:
                (output / "pocket-bound-config.yaml").write_bytes(config_bytes)
            startup["cuda_check_and_config"] = time.perf_counter() - phase_begin
            phase_begin = time.perf_counter()
            model = load_bound_model(config).cpu().eval()
            startup["tts_model_load"] = time.perf_counter() - phase_begin
            phase_begin = time.perf_counter()
            model.has_voice_cloning = False
            voice = model.get_state_for_audio_prompt(
                model_root / "embeddings/alba.safetensors"
            )
            execution = apply_execution(model, selected_execution)
            if execution is not None:
                identity["execution_precision"] = execution
            self.adapter = PocketBackend(model, voice)
            identity.update(
                device="cpu",
                threads=torch.get_num_threads(),
                seed=self.adapter.seed,
                config_sha256=hashlib.sha256(config_bytes).hexdigest(),
            )
            tts_ready = time.perf_counter() - begun
            startup["tts_voice_binding"] = time.perf_counter() - phase_begin
            phase_begin = time.perf_counter()
            self.endpoint = SmartTurn(
                root, assets=assets, frontend=endpoint_selection(runtime_root)
            )
            startup["semantic_endpoint_startup"] = time.perf_counter() - phase_begin
            startup["semantic_endpoint_phases"] = self.endpoint.startup_seconds
            phase_begin = time.perf_counter()
            self.recognizer.await_ready()
            if native_stt and self.recognizer.ready.get("backend") != "native-directml":
                raise RuntimeError("Native recognizer selection was not honored")
            startup["recognizer_readiness_wait"] = time.perf_counter() - phase_begin
            self.identity = {
                "backend": "windows-pocket-cpu-stt-directml"
                if native_stt
                else "windows-pocket-cpu-stt-cuda",
                "models": {
                    "tts": identity,
                    "stt": self.recognizer.ready,
                    "semantic_endpoint": self.endpoint.identity,
                    "vad": self.control_vad_factory().identity,
                },
                "tts_reference_sha256": ASSETS[-1][3],
                "packages": {
                    p: importlib.metadata.version(p)
                    for p in (
                        "torch",
                        "pydantic",
                        "sentencepiece",
                        "onnxruntime-directml",
                    )
                },
                "stt_control": "separate_process_environment_and_executor",
                "startup_schedule": "independent_stt_process_overlaps_cpu_models",
                "stt_preroll_frames": self.stt_preroll_frames,
                "startup_seconds": {
                    "tts_bound_loaded": tts_ready,
                    "models_ready": time.perf_counter() - begun,
                    "phases": startup,
                },
            }
            if record_observations:
                (output / "model-identity.json").write_text(
                    json.dumps(self.identity, indent=2), encoding="utf8"
                )
        except BaseException:
            self.close()
            raise

    def tts_stream(self, text):
        return self.adapter.tts_stream(text)

    def cancel_synthesis(self):
        if self.adapter is not None:
            self.adapter.cancel_synthesis()
