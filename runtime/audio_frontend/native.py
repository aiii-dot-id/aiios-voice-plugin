"""Thin ctypes adapter for exercising the portable frontend ABI from Python."""

from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path
from typing import Self

import numpy as np

from .reference import FrontendConfig, parse_manifest

HERE = Path(__file__).resolve().parent
HEADER_DIR = HERE / "include"
SOURCE = HERE / "src" / "aiii_voice_frontend.c"
FRONTEND_ABI_VERSION = 1


def build_shared_library(output: Path, *, compiler: str = "cc") -> Path:
    """Build the dependency-light C core with strict compiler diagnostics."""
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    shared_flag = "-dynamiclib" if sys.platform == "darwin" else "-shared"
    subprocess.run(
        [
            compiler,
            "-std=c99",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-pedantic",
            "-fPIC",
            "-DAIII_VOICE_FRONTEND_BUILD",
            shared_flag,
            "-I",
            str(HEADER_DIR),
            str(SOURCE),
            "-o",
            str(output),
            "-lm",
        ],
        check=True,
    )
    return output


class NativeFrontend:
    """Owned handle for one native frontend instance."""

    def __init__(self, library: Path, manifest: bytes):
        self.config: FrontendConfig = parse_manifest(manifest)
        self._library = ctypes.CDLL(str(library))
        self._configure_signatures()
        observed_abi = int(self._library.vf_frontend_abi_version())
        if observed_abi != FRONTEND_ABI_VERSION:
            raise RuntimeError(
                "portable frontend ABI differs: "
                f"library={observed_abi}, binding={FRONTEND_ABI_VERSION}"
            )
        self._handle = ctypes.c_void_p()
        manifest_buffer = (ctypes.c_uint8 * len(manifest)).from_buffer_copy(manifest)
        status = self._library.vf_frontend_create(
            manifest_buffer, len(manifest), ctypes.byref(self._handle)
        )
        self._check(status)
        observed_bins = int(self._library.vf_frontend_feature_bins(self._handle))
        if observed_bins != self.config.mel_bins:
            self.close()
            raise RuntimeError(
                "portable frontend feature dimension differs: "
                f"library={observed_bins}, manifest={self.config.mel_bins}"
            )

    def _configure_signatures(self) -> None:
        library = self._library
        library.vf_frontend_abi_version.argtypes = []
        library.vf_frontend_abi_version.restype = ctypes.c_uint32
        library.vf_frontend_create.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        library.vf_frontend_create.restype = ctypes.c_int
        library.vf_frontend_feature_bins.argtypes = [ctypes.c_void_p]
        library.vf_frontend_feature_bins.restype = ctypes.c_size_t
        library.vf_frontend_push_frame_capacity.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        library.vf_frontend_push_frame_capacity.restype = ctypes.c_size_t
        library.vf_frontend_flush_frame_capacity.argtypes = [ctypes.c_void_p]
        library.vf_frontend_flush_frame_capacity.restype = ctypes.c_size_t
        common = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        library.vf_frontend_push.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        library.vf_frontend_push.restype = ctypes.c_int
        library.vf_frontend_flush.argtypes = common
        library.vf_frontend_flush.restype = ctypes.c_int
        library.vf_frontend_reset.argtypes = [ctypes.c_void_p]
        library.vf_frontend_reset.restype = ctypes.c_int
        library.vf_frontend_destroy.argtypes = [ctypes.c_void_p]
        library.vf_frontend_destroy.restype = None
        library.vf_status_message.argtypes = [ctypes.c_int]
        library.vf_status_message.restype = ctypes.c_char_p

    def _check(self, status: int) -> None:
        if status != 0:
            message = self._library.vf_status_message(status).decode("ascii")
            raise RuntimeError(f"portable frontend failed: {message} ({status})")

    @property
    def abi_version(self) -> int:
        return int(self._library.vf_frontend_abi_version())

    def _output(self, frames: int) -> tuple[np.ndarray, ctypes.Array[ctypes.c_float]]:
        values = frames * self.config.mel_bins
        storage = (ctypes.c_float * max(1, values))()
        return np.ctypeslib.as_array(storage)[:values].reshape(
            frames, self.config.mel_bins
        ), storage

    def push(self, pcm_s16le: bytes) -> np.ndarray:
        if len(pcm_s16le) % 2:
            raise ValueError("PCM byte length must be divisible by two")
        samples = len(pcm_s16le) // 2
        capacity = int(
            self._library.vf_frontend_push_frame_capacity(self._handle, samples)
        )
        if capacity == ctypes.c_size_t(-1).value:
            capacity = 0
        output, storage = self._output(capacity)
        pcm = (ctypes.c_uint8 * max(1, len(pcm_s16le)))()
        if pcm_s16le:
            ctypes.memmove(pcm, pcm_s16le, len(pcm_s16le))
        emitted = ctypes.c_size_t()
        self._check(
            self._library.vf_frontend_push(
                self._handle,
                pcm,
                samples,
                storage,
                capacity,
                ctypes.byref(emitted),
            )
        )
        if emitted.value != capacity:
            raise RuntimeError("portable frontend violated its capacity contract")
        return output.copy()

    def flush(self) -> np.ndarray:
        capacity = int(self._library.vf_frontend_flush_frame_capacity(self._handle))
        if capacity == ctypes.c_size_t(-1).value:
            capacity = 0
        output, storage = self._output(capacity)
        emitted = ctypes.c_size_t()
        self._check(
            self._library.vf_frontend_flush(
                self._handle,
                storage,
                capacity,
                ctypes.byref(emitted),
            )
        )
        if emitted.value != capacity:
            raise RuntimeError("portable frontend violated its flush capacity contract")
        return output.copy()

    def reset(self) -> None:
        self._check(self._library.vf_frontend_reset(self._handle))

    def close(self) -> None:
        if self._handle:
            self._library.vf_frontend_destroy(self._handle)
            self._handle = ctypes.c_void_p()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        handle = getattr(self, "_handle", None)
        if handle:
            self.close()
