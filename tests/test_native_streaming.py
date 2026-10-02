import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from runtime.stt.native_streaming import NativeInput, terminal_padding


class Stream:
    def __init__(self):
        self.batches = []
        self.end = False

    def set_option(self, name, value):
        assert (name, value) == ("language", "en-US")

    def accept_waveform(self, rate, values):
        assert rate == 16000
        self.batches.append(values.copy())

    def input_finished(self):
        self.end = True


class Recognizer:
    def __init__(self):
        self.stream = Stream()
        self.decoded = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.hold = False

    def create_stream(self):
        return self.stream

    def is_ready(self, stream):
        return self.decoded < len(stream.batches)

    def decode_stream(self, stream):
        self.entered.set()
        if self.hold:
            assert self.release.wait(2)
        self.decoded += 1

    def get_result(self, stream):
        return "opening words complete" if stream.end else "opening words"


def test_terminal_pad_is_separate_from_captured_speech():
    recognizer = Recognizer()
    stream = NativeInput(recognizer)
    assert stream.push(np.ones(512, dtype=np.float32)) == ["opening words"]
    assert stream.finish() == "opening words complete"
    assert stream.samples == 512
    assert stream.model_padding_samples == terminal_padding(65) == 10560
    assert np.count_nonzero(recognizer.stream.batches[-1]) == 0
    assert len(recognizer.stream.batches[-1]) == 10560
    with pytest.raises(RuntimeError):
        stream.push(np.ones(32))
    with pytest.raises(RuntimeError):
        stream.finish()


def test_cancellation_does_not_wait_for_decode_or_publish_its_result():
    recognizer = Recognizer()
    recognizer.hold = True
    stream = NativeInput(recognizer)
    with ThreadPoolExecutor(1) as executor:
        future = executor.submit(stream.push, np.ones(512))
        assert recognizer.entered.wait(1)
        try:
            stream.cancel()
            assert stream.cancelled.is_set() and not future.done()
        finally:
            recognizer.release.set()
        assert future.result(timeout=1) == []
    with pytest.raises(RuntimeError):
        stream.finish()


def test_empty_nonfinite_and_excess_input_refused():
    stream = NativeInput(Recognizer(), maximum_samples=512)
    with pytest.raises(ValueError):
        stream.finish()
    for invalid in ([], [float("nan")], np.zeros((2, 2)), np.zeros(513)):
        with pytest.raises(ValueError):
            stream.push(invalid)
    assert stream.samples == 0


@pytest.mark.parametrize("window", [True, 0, -1, 129, 1.5])
def test_invalid_window_refused(window):
    with pytest.raises(ValueError):
        terminal_padding(window)
