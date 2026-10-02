"""Private native pause classifier; no Python Torch/Transformers or model fallback.

One inference owner, an independent cancellation lane, and explicit bounded
retirement. The existing audio-clock PauseGate remains the decision owner.
"""

import ctypes
import hashlib
import os
import sys
import threading
import time
from concurrent.futures import CancelledError

import numpy as np

from runtime.model_assets import checked, file_hashes

FP = ctypes.POINTER(ctypes.c_float)


def open_library(path):
    if sys.platform != "win32" or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise ValueError("native ATen endpoint is qualified only for Windows amd64")
    return ctypes.CDLL(str(path), winmode=0x1100)


def bind(lib):
    signatures = {
        "create": ([ctypes.c_void_p, ctypes.c_size_t, FP, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t], ctypes.c_void_p),
        "score": ([ctypes.c_void_p, ctypes.c_uint64, FP, ctypes.c_size_t, ctypes.POINTER(ctypes.c_double), FP,
                   ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t], ctypes.c_int),
        "cancel_through": ([ctypes.c_void_p, ctypes.c_uint64], ctypes.c_int),
        "phase": ([ctypes.c_void_p], ctypes.c_int),
        "destroy": ([ctypes.c_void_p], None),
    }
    for name, (args, result) in signatures.items():
        function = getattr(lib, "aii_endpoint_" + name)
        function.argtypes, function.restype = args, result


class NativeEndpoint:
    def __init__(self, config, assets):
        begin = time.perf_counter()
        self._condition = threading.Condition()
        self._handle = None
        self._active = False
        self._closing = False
        self._query = self._cancelled = 0
        self._fault = None
        # Recheck immediately before loading. The activation owns immutable
        # roots; this does not claim safety against a concurrent root writer.
        for name, row in config["files"].items():
            if file_hashes(checked(config["root"], name), row["bytes"])[0] != row["sha256"]:
                raise ValueError("native endpoint bytes changed before load")
        snapshot = assets.snapshot("endpoint")
        model_path = checked(snapshot, "smart-turn-v3.2-cpu.onnx")
        model = model_path.read_bytes()
        if hashlib.sha256(model).hexdigest() != assets.groups["endpoint"]["files"][model_path.name]["sha256"]:
            raise ValueError("native endpoint model changed after verification")
        raw = config["coefficients"].read_bytes()
        if len(raw) != 65920 or hashlib.sha256(raw).hexdigest() != config["coefficients_sha256"]:
            raise ValueError("native endpoint coefficients changed after verification")
        coefficients = np.frombuffer(raw, dtype="<f4").copy()
        if not np.isfinite(coefficients).all():
            raise ValueError("native endpoint coefficients are not finite")
        self._lib = open_library(config["library"])
        bind(self._lib)
        os.environ["ORT_DISABLE_TELEMETRY"] = "1"
        error = ctypes.create_string_buffer(2048)
        handle = self._lib.aii_endpoint_create(model, len(model), coefficients.ctypes.data_as(FP), len(coefficients), error, len(error))
        if not handle:
            raise RuntimeError("native endpoint creation failed: " + error.value.decode("utf-8", "replace"))
        self._handle = handle
        self.startup_seconds = {"total": time.perf_counter() - begin}
        self.identity = {"backend": "native-aten-cpu", "library_sha256": config["library_sha256"],
                         "coefficients_sha256": config["coefficients_sha256"],
                         "model_sha256": hashlib.sha256(model).hexdigest(),
                         "manifest_sha256": assets.manifest_sha("endpoint"),
                         "python_torch_required": False, "pause_policy": "unchanged_audio_clock_owner"}

    def score(self, audio, *, features=False):
        samples = np.array(audio, dtype=np.float32, copy=True)
        if samples.ndim != 1 or not 0 < len(samples) <= 960000 or not np.isfinite(samples).all():
            raise ValueError("native endpoint requires bounded finite mono PCM")
        with self._condition:
            if self._closing or self._handle is None or self._fault:
                raise RuntimeError("native endpoint closed or faulted")
            if self._active:
                raise RuntimeError("native endpoint already has an inference owner")
            if self._query == 2**64 - 1:
                raise RuntimeError("native endpoint query space exhausted")
            self._query += 1
            query, handle = self._query, self._handle
            self._active = True
        try:
            probability = ctypes.c_double(float("nan"))
            output = np.empty((1, 80, 800), dtype=np.float32) if features else None
            error = ctypes.create_string_buffer(2048)
            code = self._lib.aii_endpoint_score(handle, query, samples.ctypes.data_as(FP), len(samples),
                ctypes.byref(probability), output.ctypes.data_as(FP) if features else None,
                output.size if features else 0, error, len(error))
            with self._condition:
                if code == 3 or self._closing or query <= self._cancelled:
                    raise CancelledError("native endpoint query cancelled; no probability published")
                if code:
                    self._fault = error.value.decode("utf-8", "replace") or f"native score code {code}"
                    raise RuntimeError(self._fault)
                if not np.isfinite(probability.value) or not 0 <= probability.value <= 1 or (features and not np.isfinite(output).all()):
                    self._fault = "native endpoint returned invalid output"
                    raise RuntimeError(self._fault)
                return (probability.value, output) if features else probability.value
        finally:
            with self._condition:
                self._active = False
                self._condition.notify_all()

    def probability(self, audio):
        return self.score(audio)

    def cancel(self):
        with self._condition:
            if self._handle is not None and self._query:
                code = self._lib.aii_endpoint_cancel_through(self._handle, self._query)
                self._cancelled = self._query
                if code:
                    self._fault = f"native endpoint cancel failed: {code}"
                    raise RuntimeError(self._fault)

    def phase(self):
        with self._condition:
            return self._lib.aii_endpoint_phase(self._handle) if self._handle is not None else 0

    def close(self, timeout=2.0):
        with self._condition:
            self._closing = True
            self.cancel()
            if not self._condition.wait_for(lambda: not self._active, timeout):
                # Keep the handle alive. A timed out join is not destruction.
                raise TimeoutError("native endpoint inference has not retired")
            if self._handle is not None:
                self._lib.aii_endpoint_destroy(self._handle)
                self._handle = None
