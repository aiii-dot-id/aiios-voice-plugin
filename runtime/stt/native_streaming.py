"""Private Torch-free Nemotron streaming candidate, not a deployed backend.

The resident recognizer is supplied by its owner after model/runtime binding.
This adapter owns one input stream's exact captured sample count and explicit
model-only terminal padding. Its cancellation signal never needs the decoder
lock. It suppresses results when cancellation is observed after a decode;
the eventual output owner must still fence publication against its generation.
"""

import threading

import numpy as np


def terminal_padding(window_frames, hop_samples=160):
    """Strict IsReady inequality needs one frame beyond the exported window.

    The pinned 560ms encoder exports window_size=65, chunk_shift=56. Its native
    IsReady is processed + ChunkSize < NumFramesReady, including after EOF. Feed
    66 frames (10,560 samples) only to the model, consistent with upstream's 0.66s
    terminal pad. These samples are never capture, VAD, UID or transcript duration.
    """
    if type(window_frames) is not int or not 1 <= window_frames <= 128:
        raise ValueError("Bounded integer model window required")
    if hop_samples != 160:
        raise ValueError("Candidate requires the pinned 10ms/16kHz frontend")
    return (window_frames + 1) * hop_samples


class NativeInput:
    """One inference owner calls push/finish; control may call cancel anytime."""

    def __init__(self, recognizer, *, window_frames=65, maximum_samples=16000 * 60):
        if type(maximum_samples) is not int or not 1 <= maximum_samples <= 16000 * 60:
            raise ValueError("Input capacity outside bounded candidate contract")
        self.recognizer = recognizer
        self.stream = recognizer.create_stream()
        self.stream.set_option("language", "en-US")
        self.window_frames = window_frames
        self._terminal_pad = terminal_padding(window_frames)
        self.maximum_samples = maximum_samples
        self.samples = 0
        self.model_padding_samples = 0
        self.finished = False
        self.cancelled = threading.Event()
        self._text = ""

    def cancel(self):
        self.cancelled.set()

    def _open(self):
        if self.cancelled.is_set() or self.finished:
            raise RuntimeError("Input is closed or cancelled")

    def _decode(self):
        results = []
        while not self.cancelled.is_set() and self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)
            if self.cancelled.is_set():
                return []
            text = self.recognizer.get_result(self.stream)
            if text and text != self._text:
                results.append(text)
                self._text = text
        return [] if self.cancelled.is_set() else results

    def push(self, samples):
        self._open()
        samples = np.asarray(samples, dtype=np.float32)
        if (
            samples.ndim != 1
            or not 0 < len(samples) <= 32000
            or not np.isfinite(samples).all()
        ):
            raise ValueError(
                "Finite mono PCM in batches of at most two seconds required"
            )
        if self.samples + len(samples) > self.maximum_samples:
            raise ValueError("Input exceeds bounded duration")
        self.stream.accept_waveform(16000, samples)
        self.samples += len(samples)
        return self._decode()

    def finish(self):
        self._open()
        if not self.samples:
            raise ValueError("Empty input has no recognition evidence")
        self.finished = True
        self.model_padding_samples = self._terminal_pad
        self.stream.accept_waveform(
            16000, np.zeros(self.model_padding_samples, dtype=np.float32)
        )
        self.stream.input_finished()
        self._decode()
        if self.cancelled.is_set():
            raise RuntimeError("Input cancelled during final decode")
        return self.recognizer.get_result(self.stream)
