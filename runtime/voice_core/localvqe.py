"""Isolated pretrained echo experiment; no live loader selects this backend."""

import ctypes
import hashlib
import json

import numpy as np

HOP = 256
PACKET = 512
MODEL = "localvqe-v1.4-aec-200K-f32.gguf"
MODEL_SHA = "b6e43138588a83bfe903ab5e143b4020b91c1e1629f5a575ac5855ff0003c731"
SOURCE = "f53063c9eb2a85f96479867d1dd911dc3bf6319b"


class NativeLocalVQE:
    def __init__(self, root):
        manifest = json.loads((root / "native-binding.json").read_text())
        if manifest["source_revision"] != SOURCE or not manifest["files"]:
            raise ValueError("LocalVQE source binding differs")
        for name, digest in manifest["files"].items():
            if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
                raise ValueError("LocalVQE native file differs: " + name)
        model = root / "models" / MODEL
        if (
            model.stat().st_size != 2924224
            or hashlib.sha256(model.read_bytes()).hexdigest() != MODEL_SHA
        ):
            raise ValueError("LocalVQE model differs")
        self.lib = ctypes.CDLL(str(root / manifest["library"]))
        handle, fp = ctypes.c_size_t, ctypes.POINTER(ctypes.c_float)
        specs = {
            "localvqe_options_new": ([], handle),
            "localvqe_options_free": ([handle], None),
            "localvqe_options_set_model_path": (
                [handle, ctypes.c_char_p],
                ctypes.c_int,
            ),
            "localvqe_options_set_backend": ([handle, ctypes.c_char_p], ctypes.c_int),
            "localvqe_options_set_threads": ([handle, ctypes.c_int], ctypes.c_int),
            "localvqe_new_with_options": ([handle], handle),
            "localvqe_free": ([handle], None),
            "localvqe_reset": ([handle], None),
            "localvqe_hop_length": ([handle], ctypes.c_int),
            "localvqe_sample_rate": ([handle], ctypes.c_int),
            "localvqe_get_noise_gate": (
                [handle, ctypes.POINTER(ctypes.c_int), fp],
                ctypes.c_int,
            ),
            "localvqe_process_frame_f32": (
                [handle, fp, fp, ctypes.c_int, fp],
                ctypes.c_int,
            ),
        }
        for name, (arguments, returns) in specs.items():
            fn = getattr(self.lib, name)
            fn.argtypes, fn.restype = arguments, returns
        opts, self.ctx = self.lib.localvqe_options_new(), 0
        if not opts:
            raise RuntimeError("LocalVQE options allocation failed")
        try:
            for code in (
                self.lib.localvqe_options_set_model_path(opts, str(model).encode()),
                self.lib.localvqe_options_set_backend(opts, b"CPU"),
                self.lib.localvqe_options_set_threads(opts, 2),
            ):
                if code:
                    raise RuntimeError("LocalVQE options refused")
            self.ctx = self.lib.localvqe_new_with_options(opts)
        finally:
            self.lib.localvqe_options_free(opts)
        try:
            if (
                not self.ctx
                or self.lib.localvqe_hop_length(self.ctx) != HOP
                or self.lib.localvqe_sample_rate(self.ctx) != 16000
            ):
                raise RuntimeError("LocalVQE load/format mismatch")
            enabled, threshold = ctypes.c_int(), ctypes.c_float()
            code = self.lib.localvqe_get_noise_gate(
                self.ctx, ctypes.byref(enabled), ctypes.byref(threshold)
            )
            if code or enabled.value:
                raise RuntimeError("LocalVQE noise gate is not disabled")
            self.identity = {
                "source": SOURCE,
                "model_sha256": MODEL_SHA,
                "backend": "CPU",
                "threads": 2,
                "noise_gate": False,
                "binding_sha256": hashlib.sha256(
                    (root / "native-binding.json").read_bytes()
                ).hexdigest(),
            }
        except BaseException:
            self.close()
            raise

    def process(self, mic, render):
        if not self.ctx:
            raise ValueError("LocalVQE closed")
        mic, render = (
            np.ascontiguousarray(mic, np.float32),
            np.ascontiguousarray(render, np.float32),
        )
        if (
            mic.shape != (HOP,)
            or render.shape != (HOP,)
            or not np.isfinite(mic).all()
            or not np.isfinite(render).all()
        ):
            raise ValueError("LocalVQE requires finite mono hops")
        output = np.empty(HOP, np.float32)
        fp = ctypes.POINTER(ctypes.c_float)
        code = self.lib.localvqe_process_frame_f32(
            self.ctx,
            mic.ctypes.data_as(fp),
            render.ctypes.data_as(fp),
            HOP,
            output.ctypes.data_as(fp),
        )
        if code or not np.isfinite(output).all():
            raise RuntimeError("LocalVQE processing failed")
        return output

    def reset(self):
        if not self.ctx:
            raise ValueError("LocalVQE closed")
        self.lib.localvqe_reset(self.ctx)

    def close(self):
        if self.ctx:
            self.lib.localvqe_free(self.ctx)
            self.ctx = 0


class LocalVQEStream:
    """Account for the previous-hop codec origin, independent of input batching."""

    def __init__(self, processor):
        self.processor = processor
        self.mic = np.empty(0, np.float32)
        self.ref = np.empty(0, np.float32)
        self.ready = np.empty(0, np.float32)
        self.skip = HOP
        self.received = self.produced = self.padding_samples = 0
        self.ended = False

    def _frame(self, mic, render):
        output = self.processor.process(mic, render)
        if output.shape != (HOP,) or not np.isfinite(output).all():
            raise ValueError("invalid LocalVQE output hop")
        skip = min(self.skip, len(output))
        self.skip -= skip
        self.ready = np.r_[self.ready, output[skip:]]

    def _take(self, final=False):
        count = min(len(self.ready), self.received - self.produced)
        if not final:
            count = count // PACKET * PACKET
        parts = [
            self.ready[i : min(i + PACKET, count)].copy()
            for i in range(0, count, PACKET)
        ]
        self.ready = self.ready[count:].copy()
        self.produced += count
        return parts

    def push(self, mic, render):
        mic, render = np.asarray(mic, np.float32), np.asarray(render, np.float32)
        if (
            self.ended
            or mic.ndim != 1
            or mic.shape != render.shape
            or not 0 < len(mic) <= PACKET
            or not np.isfinite(mic).all()
            or not np.isfinite(render).all()
        ):
            raise ValueError("bounded finite input before finish required")
        self.received += len(mic)
        self.mic, self.ref = np.r_[self.mic, mic], np.r_[self.ref, render]
        while len(self.mic) >= HOP:
            self._frame(self.mic[:HOP], self.ref[:HOP])
            self.mic, self.ref = self.mic[HOP:].copy(), self.ref[HOP:].copy()
        return self._take()

    def finish(self):
        if self.ended:
            return []
        self.ended = True
        if len(self.mic):
            padding = HOP - len(self.mic)
            self._frame(np.pad(self.mic, (0, padding)), np.pad(self.ref, (0, padding)))
            self.padding_samples += padding
        if self.received:
            self._frame(np.zeros(HOP, np.float32), np.zeros(HOP, np.float32))
            self.padding_samples += HOP
        result = self._take(final=True)
        if self.produced != self.received:
            raise RuntimeError("LocalVQE real tail did not drain")
        self.ready = self.mic = self.ref = np.empty(0, np.float32)
        return result
