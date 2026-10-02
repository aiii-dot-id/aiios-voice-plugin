"""Explicit WASAPI audio; bounded callbacks, no model execution or global routing.

Written samples are PortAudio callback submissions, not measured acoustic output.
Stop and drain resolve only after the last submitted buffer's reported DAC time.
This supplies no AEC: use headphones or a separately qualified echo frontend.
"""

import hashlib
import json
import sys
import threading
from builtins import BaseExceptionGroup, ExceptionGroup
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import numpy as np


def validate_capture_mode(value):
    if value not in ("shared", "exclusive"):
        raise ValueError("Explicit capture_mode must be shared or exclusive")
    return value


def devices(sd):
    apis = sd.query_hostapis()
    rows = []
    for index, item in enumerate(sd.query_devices()):
        if apis[item["hostapi"]]["name"] != "Windows WASAPI":
            continue
        identity = {
            "name": item["name"],
            "api": "Windows WASAPI",
            "input_channels": item["max_input_channels"],
            "output_channels": item["max_output_channels"],
        }
        uid = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        rows.append({"uid": uid, "index": index, **identity})
    return rows


class AudioState:
    """Bounded coordinator state shared only with short audio callbacks."""

    def __init__(self):
        self.lock = threading.Lock()
        self.input, self.output = deque(), deque()
        self.captured = self.read_count = self.submitted = self.written = 0
        self.discarded = self.epoch = self.state = self.holes = self.underflows = 0
        self.input_finished = False
        self.target = -1
        self.deadline = 0.0
        self.error = None

    def capture(self, pcm, overflow=False):
        with self.lock:
            if overflow:
                self.holes += 1
                self.error = "WASAPI input overflow"
            if self.input_finished:
                return
            if self.captured - self.read_count + len(pcm) > 32000:
                self.error = "WASAPI input queue exceeded two seconds"
                return
            self.input.append(pcm.copy())
            self.captured += len(pcm)

    def read(self, count=512):
        if type(count) is not int or not 0 < count <= 32000:
            raise ValueError("Bounded input read required")
        chunks = []
        with self.lock:
            while count and self.input:
                part = self.input.popleft()
                n = min(count, len(part))
                chunks.append(part[:n])
                if n < len(part):
                    self.input.appendleft(part[n:])
                count -= n
                self.read_count += n
        return np.concatenate(chunks) if chunks else np.empty(0, np.float32)

    def finish_input(self):
        with self.lock:
            if not self.input_finished:
                self.input_finished, self.target = True, self.captured
            return self.target

    def begin(self, epoch):
        with self.lock:
            if type(epoch) is not int or epoch <= self.epoch or self.state:
                raise ValueError("Idle host and fresh positive epoch required")
            self.epoch, self.state = epoch, 1
            self.submitted = self.written = self.discarded = 0
            self.deadline = 0

    def write(self, epoch, pcm):
        pcm = np.asarray(pcm, dtype=np.float32)
        if (
            pcm.ndim != 1
            or not 0 < len(pcm) <= 48000
            or not np.isfinite(pcm).all()
            or np.max(np.abs(pcm)) > 1.001
        ):
            raise ValueError("Bounded finite normalized PCM required")
        with self.lock:
            if epoch != self.epoch or self.state != 1:
                raise ValueError("Stale or closed playback epoch")
            if self.submitted - self.written + len(pcm) > 48000:
                raise ValueError("Output queue exceeded two seconds")
            self.output.append(pcm.copy())
            self.submitted += len(pcm)

    def end(self, epoch):
        with self.lock:
            if epoch != self.epoch or self.state != 1:
                raise ValueError("Stale or closed end")
            self.state = 2

    def stop(self, epoch):
        with self.lock:
            if epoch != self.epoch or self.state not in (1, 2):
                raise ValueError("Stale or inactive stop")
            self.discarded += self.submitted - self.written
            self.output.clear()
            self.state = 3

    def render(self, count, dac_time, now, underflow=False):
        pcm = np.zeros(count, np.float32)
        with self.lock:
            if underflow:
                self.underflows += 1
            offset = 0
            if self.state in (1, 2):
                while offset < count and self.output:
                    part = self.output.popleft()
                    n = min(len(part), count - offset)
                    pcm[offset : offset + n] = part[:n]
                    if n < len(part):
                        self.output.appendleft(part[n:])
                    offset += n
                self.written += offset
                if offset:
                    self.deadline = dac_time + offset / 24000
            if self.state in (2, 3) and not self.output and now >= self.deadline:
                self.state = 0
        return pcm

    def status(self):
        with self.lock:
            if self.error:
                raise RuntimeError(self.error)
            return {
                "epoch": self.epoch,
                "state": self.state,
                "playback": ("idle", "active", "draining", "stopping")[self.state],
                "captured": self.captured,
                "read": self.read_count,
                "submitted": self.submitted,
                "written": self.written,
                "discarded": self.discarded,
                "capture_holes": self.holes,
                "underflows": self.underflows,
                "input_finished": self.input_finished,
                "input_target": self.target,
                "input_available": self.captured - self.read_count,
                "output_available": 48000
                - (self.submitted - self.written - self.discarded),
            }


def initialize_com():
    """Own one MTA reference on the calling lifecycle thread; balance there."""
    import ctypes

    ole = ctypes.OleDLL("ole32")
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    ole.CoInitializeEx(None, 0)  # HRESULT failure raises, including changed mode.
    return ole.CoUninitialize


class WASAPIAudio(AudioState):
    def __init__(self, *, source, sink, capture_mode="shared"):

        super().__init__()
        validate_capture_mode(capture_mode)
        if sys.platform != "win32":
            raise RuntimeError("WASAPI requires Windows")
        self.capture_stream = self.playback_stream = None
        self.sd = self.uninitialize_com = None
        self.lifecycle_lock = threading.Lock()
        self.lifecycle_closed = False
        self.lifecycle = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="wasapi-lifecycle"
        )
        try:
            self.lifecycle.submit(
                self.open_streams, source, sink, capture_mode
            ).result()
        except BaseException:
            self.lifecycle.shutdown(wait=True)
            raise

    def open_streams(self, source, sink, capture_mode):
        # Device operations stay on this COM-initialized owner, independent of
        # inference and callback/control locks. Generic asyncio workers have no
        # COM apartment, and close may arrive on a different generic worker.
        self.uninitialize_com = initialize_com()
        try:
            self.create_streams(source, sink, capture_mode)
        except BaseException as original:
            if isinstance(original, getattr(self.sd, "PortAudioError", ())):
                info = self.sd._lib.Pa_GetLastHostErrorInfo()
                text = self.sd._ffi.string(info.errorText).decode(errors="replace")
                original = RuntimeError(
                    f"{original}; WASAPI host error {int(info.errorCode)}: {text}"
                )
            try:
                self.close_streams()
            except BaseException as cleanup:  # noqa: BLE001 -- retain both failures after cleanup
                raise BaseExceptionGroup(
                    "WASAPI open and cleanup failed", [original, cleanup]
                ) from None
            raise original  # noqa: TRY201 -- may include the native HRESULT

    def create_streams(self, source, sink, capture_mode):
        import sounddevice as sd

        rows = devices(sd)

        def select(uid, direction):
            matches = [
                r for r in rows if r["uid"] == uid and r[direction + "_channels"] > 0
            ]
            if len(matches) != 1:
                raise ValueError("Exact unique WASAPI endpoint required")
            return matches[0]["index"]

        self.defaults = tuple(sd.default.device)
        self.sd = sd
        if source is not None:
            self.capture_stream = sd.InputStream(
                device=select(source, "input"),
                channels=1,
                samplerate=16000,
                dtype="float32",
                blocksize=320,
                latency="low",
                extra_settings=sd.WasapiSettings(
                    auto_convert=capture_mode == "shared",
                    exclusive=capture_mode == "exclusive",
                ),
                callback=self.capture_callback,
            )
        self.playback_stream = sd.OutputStream(
            device=select(sink, "output"),
            channels=1,
            samplerate=24000,
            dtype="float32",
            blocksize=480,
            latency="low",
            extra_settings=sd.WasapiSettings(auto_convert=True),
            callback=self.render_callback,
        )
        self.playback_stream.start()
        if self.capture_stream:
            self.capture_stream.start()

    def capture_callback(self, data, frames, timing, status):
        try:
            self.capture(data[:, 0], bool(status.input_overflow))
        except BaseException as error:  # noqa: BLE001 -- a callback failure must surface to its owner
            self.error = repr(error)

    def render_callback(self, data, frames, timing, status):
        try:
            data[:, 0] = self.render(
                frames,
                timing.outputBufferDacTime,
                timing.currentTime,
                bool(status.output_underflow),
            )
        except BaseException as error:  # noqa: BLE001 -- silence output and fault its owner
            data.fill(0)
            self.error = repr(error)

    def close(self):
        with self.lifecycle_lock:
            if self.lifecycle_closed:
                return
            self.lifecycle_closed = True
            try:
                self.lifecycle.submit(self.close_streams).result()
            finally:
                self.lifecycle.shutdown(wait=True)

    def close_streams(self):
        errors = []
        for name in ("capture_stream", "playback_stream"):
            stream = getattr(self, name)
            setattr(self, name, None)
            if stream is not None:
                try:
                    stream.abort()
                except Exception as error:  # noqa: BLE001 -- close the other resources too
                    errors.append(error)
                try:
                    stream.close()
                except Exception as error:  # noqa: BLE001 -- balance COM even on driver failure
                    errors.append(error)
        if self.sd is not None and tuple(self.sd.default.device) != self.defaults:
            errors.append(
                RuntimeError("Audio defaults changed during explicit endpoint session")
            )
        if self.uninitialize_com is not None:
            uninitialize, self.uninitialize_com = self.uninitialize_com, None
            uninitialize()
        if errors:
            raise ExceptionGroup("WASAPI cleanup failed", errors)
