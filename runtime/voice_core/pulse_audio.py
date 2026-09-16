"""Explicit Linux endpoints; native control has no model-thread dependency."""

from __future__ import annotations

import ctypes as c
import sys
from pathlib import Path

import numpy as np


class Status(c.Structure):
    _fields_ = (
        [
            (name, c.c_uint64)
            for name in (
                "epoch",
                "captured",
                "read",
                "submitted",
                "written",
                "discarded",
                "rejected",
                "input_ack_ns",
                "output_ack_ns",
                "capture_holes",
                "underflows",
                "input_after_seal",
            )
        ]
        + [
            (name, c.c_uint32)
            for name in (
                "input_available",
                "output_available",
                "state",
                "input_finished",
                "failed",
            )
        ]
        + [
            ("corked", c.c_int32),
            ("drain_state", c.c_int32),
            ("read_index", c.c_int64),
            ("write_index", c.c_int64),
            ("input_target", c.c_int64),
        ]
    )


class PulseAudio:
    """Opening this object starts the explicitly selected source, never a default.

    One coordinator owns lifecycle/close. Concurrent capture, output and control
    calls are safe; close must follow their joins. Captured PCM is 16k mono,
    output is 24k mono. Sample counts and server acknowledgments are not a claim
    about hardware clock accuracy or audible output.
    """

    def __init__(self, library, *, sink, source):
        if sys.platform != "linux" or not sink or not source:
            raise ValueError("Linux and explicit input/output names required")
        self.lib = c.CDLL(str(Path(library).resolve()))
        self.lib.vf_pulse_status_size.argtypes = []
        self.lib.vf_pulse_status_size.restype = c.c_size_t
        if self.lib.vf_pulse_status_size() != c.sizeof(Status):
            raise RuntimeError("native audio status ABI differs")
        signatures = {
            "create": ([c.c_char_p, c.c_char_p], c.c_void_p),
            "destroy": ([c.c_void_p], None),
            "status": ([c.c_void_p, c.POINTER(Status)], c.c_int),
            "begin": ([c.c_void_p, c.c_uint64], c.c_int),
            "write": (
                [c.c_void_p, c.c_uint64, c.POINTER(c.c_float), c.c_uint32],
                c.c_int,
            ),
            "read": ([c.c_void_p, c.POINTER(c.c_float), c.c_uint32], c.c_int),
            "end": ([c.c_void_p, c.c_uint64], c.c_int),
            "stop": ([c.c_void_p, c.c_uint64], c.c_int),
            "finish_input": ([c.c_void_p], c.c_int),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.lib, "vf_pulse_" + name)
            function.argtypes, function.restype = args, result
        self.handle = self.lib.vf_pulse_create(sink.encode(), source.encode())
        if not self.handle:
            raise RuntimeError("explicit native audio endpoints failed to open")

    def _call(self, name, *args):
        if not self.handle:
            raise RuntimeError("native audio is closed")
        result = getattr(self.lib, "vf_pulse_" + name)(self.handle, *args)
        if result < 0:
            raise RuntimeError(f"native audio {name} refused ({result})")
        return result

    def status(self):
        state = Status()
        self._call("status", c.byref(state))
        result = {name: getattr(state, name) for name, _ in Status._fields_}
        if result["failed"]:
            raise RuntimeError(f"native audio failed: {result}")
        result["playback"] = ("idle", "active", "draining", "stopping")[result["state"]]
        return result

    def begin(self, epoch):
        self._epoch(epoch)
        self._call("begin", epoch)

    @staticmethod
    def _epoch(epoch):
        if type(epoch) is not int or not 0 < epoch < 2**64:
            raise ValueError("positive uint64 generation epoch required")

    def write(self, epoch, pcm):
        self._epoch(epoch)
        values = np.ascontiguousarray(pcm, dtype=np.float32)
        if (
            values.ndim != 1
            or not 0 < values.size <= 48000
            or not np.isfinite(values).all()
            or np.max(np.abs(values)) > 1.001
        ):
            raise ValueError("bounded finite mono output required")
        self._call(
            "write", epoch, values.ctypes.data_as(c.POINTER(c.c_float)), len(values)
        )

    def read(self, count=512):
        if type(count) is not int or not 0 < count <= 32000:
            raise ValueError("bounded read required")
        values = np.empty(count, np.float32)
        length = self._call("read", values.ctypes.data_as(c.POINTER(c.c_float)), count)
        return values[:length]

    def end(self, epoch):
        self._epoch(epoch)
        self._call("end", epoch)

    def stop(self, epoch):
        self._epoch(epoch)
        self._call("stop", epoch)

    def finish_input(self):
        """Seal delivered PCM; preserve all unread samples through input_target.

        The server's later packets are counted as input_after_seal, not appended
        after the declared end. This does not drain microphone-internal audio.
        """
        self._call("finish_input")
        return self.status()["input_target"]

    def close(self):
        if self.handle:
            self.lib.vf_pulse_destroy(self.handle)
            self.handle = None
