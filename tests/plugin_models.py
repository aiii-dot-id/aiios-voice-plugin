"""Dependency-light deterministic fixtures for native transport tests only."""

import threading
from types import SimpleNamespace

import numpy as np


class Models:
    max_tokens = 256

    def __init__(self):
        self.identity = {"backend": "deterministic-test-not-real-model"}
        self.samples = []
        self.blocked = threading.Event()
        self.release = threading.Event()
        self.stall = False

    def control_vad_factory(self):
        return SimpleNamespace(feed=lambda audio: float(np.max(np.abs(audio)) > 0.1))

    def stt_stream(self):
        def push(audio):
            self.samples.extend(audio)
            return [
                SimpleNamespace(result=SimpleNamespace(text="cobalt lantern seventeen"))
            ]

        return SimpleNamespace(push_audio=push, finish=lambda: push([]))

    def tts_stream(self, text):
        if self.stall:
            self.blocked.set()
            if not self.release.wait(3):
                raise RuntimeError("test model timed out")
        yield np.full(1200, 0.2, np.float32), 24000, 1
        yield np.full(137, 0.3, np.float32), 24000, 1

    def tts_next(self, it):
        return next(it, None)


def open_args(sid="session-one"):
    return {
        "session_id": sid,
        "input_handle": "mic",
        "output_handle": "speaker",
        "audio": {
            "format": "s16le",
            "input": {"rate": 48000, "channels": 1},
            "output": {"rate": 48000, "channels": 2},
        },
    }
