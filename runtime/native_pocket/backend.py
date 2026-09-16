"""Private resident native Pocket adapter for the existing SpeechOutput owner.

No Torch, devices, sockets, SDK API changes or installed-runtime overrides.
Cancellation is generation-scoped and does not wait on the inference lock.
"""

import ctypes as C
import hashlib
import os
import re
import threading
from pathlib import Path

from runtime.physical_paths import existing_io_path

ASSETS = {
    "model.safetensors": "be9c6b4876d3f30740a8225dfcaa2e43dc4aeb753c15272735bee16bbb4abb0a",
    "tokenizer.model": "d461765ae179566678c93091c5fa6f2984c31bbe990bf1aa62d92c64d91bc3f6",
    "embeddings/alba.safetensors": "69c32db63ca56843d994f81f343f62e0bf2d73f7e4c9bc73e44bb1110b1d8845",
    "config.yaml": "bd8097ef21deb9f341755c8e48b8501321c70a01b5f2271075686a0b809be9bb",
}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def wire_text(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 512 or "\0" in text:
        raise ValueError("nonempty NUL-free text of at most 512 characters required")
    return text.encode("utf-8", "strict")


def selected_assets(voice, voice_assets):
    """A verified package catalog, not a path or an unbound model suggestion."""
    if voice_assets is None:
        if voice != "alba":
            raise ValueError("selected native voice has no bound catalog")
        return dict(ASSETS)
    if (not isinstance(voice_assets, dict) or not 1 <= len(voice_assets) <= 128
            or not isinstance(voice, str) or voice not in voice_assets):
        raise ValueError("selected native voice is not bound")
    for name, digest_value in voice_assets.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_-]{1,64}", name)
                or not isinstance(digest_value, str) or not re.fullmatch(r"[0-9a-f]{64}", digest_value)):
            raise ValueError("native voice ID/hash binding refused")
    if "alba" in voice_assets and voice_assets["alba"] != ASSETS["embeddings/alba.safetensors"]:
        raise ValueError("existing Alba binding changed")
    return {**{k: v for k, v in ASSETS.items() if not k.startswith("embeddings/")},
            f"embeddings/{voice}.safetensors": voice_assets[voice]}


class NativePocketBackend:
    max_tokens = 750

    def __init__(self, library, assets, *, binary_sha256, backend="vulkan", threads=4,
                 seed=20260908, max_steps=750, noise_file=None, config_path=None,
                 voice="alba", voice_assets=None):
        expected_assets = selected_assets(voice, voice_assets)
        if voice_assets is not None and config_path is None:
            raise ValueError("catalog voices require explicit bound model layout")
        self._selected_voice = voice if voice_assets is not None else None
        library, assets = existing_io_path(library), existing_io_path(assets)
        if digest(library) != binary_sha256:
            raise ValueError("native library binding changed")
        model_root = assets if config_path is not None else assets / "languages/english"
        config_path = existing_io_path(config_path) if config_path is not None else None
        for name, expected in expected_assets.items():
            path = config_path if name == "config.yaml" and config_path is not None else model_root / name
            if digest(path) != expected:
                raise ValueError("native model asset changed: " + name)
        if backend not in ("cpu", "vulkan") or type(threads) is not int or not 1 <= threads <= 4:
            raise ValueError("explicit cpu/vulkan and 1..4 threads required")
        if backend == "vulkan" and (os.environ.get("GGML_VK_DISABLE_F16") != "1" or os.environ.get("GGML_VK_VISIBLE_DEVICES") != "0"):
            raise ValueError("private Vulkan process must explicitly bind FP32/device zero")
        if type(seed) is not int or not 0 <= seed <= 2**32-1 or type(max_steps) is not int or not 1 <= max_steps <= 750:
            raise ValueError("invalid seed/frame bound")
        self.seed, self.max_steps = seed, max_steps
        self.noise_file = str(existing_io_path(noise_file)).encode("utf-8") if noise_file else None
        self._lifetime = threading.Lock()
        self._owner = threading.Lock()
        self._active = None
        self._generation = 0
        self._closed = False
        self._failed = False
        self._dll_directory = os.add_dll_directory(str(library.parent)) if os.name == "nt" else None
        # CDLL deliberately releases the GIL during native compute. PyDLL
        # would prevent Python's control/event-loop thread from cancelling it.
        try:
            self._load_library(library, assets, config_path, backend, threads)
        except BaseException:
            if self._dll_directory:
                self._dll_directory.close()
            raise
        self.identity = {"backend": "native-pocket-" + backend, "library_sha256": binary_sha256,
                         "asset_sha256": expected_assets, "preset": voice, "threads": threads,
                         "seed": seed, "max_steps": max_steps, "sample_rate": 24000,
                         "model_layout": "host_data_runtime_config" if config_path is not None else "component_bundle"}

    def _load_library(self, library, assets, config_path, backend, threads):
        self._lib = C.CDLL(str(library))
        signatures = {
            "nv_create": (C.c_void_p, [C.c_char_p, C.c_char_p, C.c_int, C.c_char_p, C.c_size_t]),
            "nv_start": (C.c_int, [C.c_void_p, C.c_uint64, C.c_char_p, C.c_uint32, C.c_int, C.c_char_p, C.c_char_p, C.c_size_t]),
            "nv_next": (C.c_int, [C.c_void_p, C.c_uint64, C.POINTER(C.c_float), C.c_size_t, C.POINTER(C.c_size_t), C.c_char_p, C.c_size_t]),
            "nv_cancel": (C.c_int, [C.c_void_p, C.c_uint64]),
            "nv_state": (C.c_uint32, [C.c_void_p]),
            "nv_reset": (C.c_int, [C.c_void_p, C.c_uint64, C.c_char_p, C.c_size_t]),
            "nv_destroy": (C.c_int, [C.c_void_p]),
        }
        if config_path is not None:
            signatures["nv_create_bound"] = (C.c_void_p, [C.c_char_p, C.c_char_p, C.c_char_p, C.c_int, C.c_char_p, C.c_size_t])
        if self._selected_voice is not None:
            signatures["nv_create_voice_bound"] = (C.c_void_p, [C.c_char_p, C.c_char_p, C.c_char_p, C.c_int, C.c_char_p, C.c_char_p, C.c_size_t])
        for name, (result, args) in signatures.items():
            fn = getattr(self._lib, name)
            fn.restype, fn.argtypes = result, args
        error = C.create_string_buffer(2048)
        arguments = [str(assets).encode("utf-8")]
        if config_path is not None:
            arguments.append(str(config_path).encode("utf-8"))
        create = self._lib.nv_create_bound if config_path is not None else self._lib.nv_create
        arguments.extend((backend.encode(), threads))
        if self._selected_voice is not None:
            create = self._lib.nv_create_voice_bound
            arguments.append(self._selected_voice.encode("ascii"))
        self._handle = create(*arguments, error, len(error))
        if not self._handle:
            raise RuntimeError(error.value.decode("utf-8", "replace"))

    def tts_stream(self, text):
        encoded = wire_text(text)
        with self._lifetime:
            if self._closed or self._failed or self._active is not None:
                raise RuntimeError("native engine closed, failed or already active")
            self._generation += 1
            if self._generation >= 2**64:
                raise RuntimeError("generation space exhausted")
            stream = NativeStream(self, self._generation, encoded)
            self._active = stream
        # Start is on the model owner, not while holding the control lock.
        # Its generation was published before this call, so early cancellation
        # survives start/preparation instead of being reset by it.
        if not self._owner.acquire(blocking=False):
            self._failed = True
            with self._lifetime:
                if self._active is stream:
                    self._active = None
            raise RuntimeError("concurrent native owner refused")
        try:
            error = C.create_string_buffer(2048)
            code = self._lib.nv_start(self._handle, stream.generation, encoded, self.seed,
                                      self.max_steps, self.noise_file, error, len(error))
            if code == -2:
                stream.cancelled.set()
            elif code != 0:
                self._failed = True
                raise RuntimeError(error.value.decode("utf-8", "replace") or "native start refused")
            return stream
        except BaseException:
            # Retire the private stream on this owner; failed handles cannot
            # be reused, but close() can still release their native resources.
            with self._lifetime:
                self._active = None
            raise
        finally:
            self._owner.release()

    @staticmethod
    def tts_next(stream):
        return stream.next()

    def cancel_synthesis(self):
        with self._lifetime:
            if self._closed or self._active is None:
                return
            stream = self._active
            stream.cancelled.set()
            if self._lib.nv_cancel(self._handle, stream.generation) != 0:
                self._failed = True
                raise RuntimeError("native cancel fence refused")

    def state(self):
        """Private diagnostic snapshot, not a replacement SDK state authority."""
        with self._lifetime:
            return 0 if self._closed else int(self._lib.nv_state(self._handle))

    def close(self):
        if not self._owner.acquire(blocking=False):
            raise RuntimeError("retire in-flight native inference before close")
        try:
            with self._lifetime:
                if self._closed:
                    return
                if self._active is not None:
                    raise RuntimeError("close the native stream before its backend")
                if self._lib.nv_destroy(self._handle) != 0:
                    self._failed = True
                    raise RuntimeError("native model retirement refused")
                self._closed, self._handle = True, None
                if self._dll_directory:
                    self._dll_directory.close()
        finally:
            self._owner.release()


class NativeStream:
    def __init__(self, owner, generation, text):
        self.owner, self.generation = owner, generation
        self.cancelled = threading.Event()
        self.closed = False
        self.buffer = (C.c_float * 1920)()

    def next(self):
        import numpy as np

        if not self.owner._owner.acquire(blocking=False):
            raise RuntimeError("concurrent native inference refused")
        try:
            if self.closed or self.cancelled.is_set():
                return None
            error, count = C.create_string_buffer(2048), C.c_size_t()
            code = self.owner._lib.nv_next(self.owner._handle, self.generation, self.buffer,
                                          len(self.buffer), C.byref(count), error, len(error))
            if code not in (0, 1, -2):
                self.owner._failed = True
                raise RuntimeError(error.value.decode("utf-8", "replace") or "native next refused")
            if self.cancelled.is_set() or code == -2:
                return None
            if code == 0:
                return None
            audio = np.ctypeslib.as_array(self.buffer)[:count.value].copy()
            if self.cancelled.is_set():
                return None
            return audio, 24000, 1
        finally:
            self.owner._owner.release()

    def close(self):
        self.cancelled.set()
        if not self.owner._owner.acquire(blocking=False):
            raise RuntimeError("retire in-flight native next before stream close")
        try:
            if not self.closed:
                error = C.create_string_buffer(2048)
                code = self.owner._lib.nv_reset(self.owner._handle, self.generation, error, len(error))
                if code != 0:
                    self.owner._failed = True
                    raise RuntimeError(error.value.decode("utf-8", "replace") or "native reset refused")
                self.closed = True
                with self.owner._lifetime:
                    if self.owner._active is self:
                        self.owner._active = None
        finally:
            self.owner._owner.release()
