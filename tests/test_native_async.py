from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from runtime.stt.native_async import NativeAsyncStream
from tests.test_native_streaming import Recognizer


def test_real_interface_finishes_whole_input_and_keeps_padding_separate():
    with ThreadPoolExecutor(1) as owner:
        stream = NativeAsyncStream(Recognizer(), owner)
        stream.push_audio(np.ones(512))
        assert stream.finish()[0].result.text == "opening words complete"
        assert stream.terminal["captured_samples"] == 512
        assert stream.terminal["model_padding_samples"] == 10560
        assert not stream.terminal["decoder_ready_after_finish"]


def test_control_does_not_wait_and_cancelled_generation_cannot_publish():
    recognizer = Recognizer()
    recognizer.hold = True
    with ThreadPoolExecutor(1) as owner:
        stream = NativeAsyncStream(recognizer, owner)
        stream.push_audio(np.ones(512))
        assert recognizer.entered.wait(1)
        try:
            stream.cancel()
            assert not stream.future.done()
            assert stream.text == ""
        finally:
            recognizer.release.set()
        assert stream.retire()["event"] == "cancelled"
        assert stream.partial_events == []
        recovery = NativeAsyncStream(Recognizer(), owner)
        recovery.push_audio(np.ones(512))
        assert recovery.finish()[0].result.text == "opening words complete"


def test_bad_input_and_empty_finish_cannot_manufacture_transcript():
    with ThreadPoolExecutor(1) as owner:
        stream = NativeAsyncStream(Recognizer(), owner)
        try:
            for invalid in ([], [float("nan")], np.zeros((2, 2))):
                with pytest.raises(ValueError):
                    stream.push_audio(invalid)
            with pytest.raises(ValueError):
                stream.finish()
        finally:
            stream.cancel()
            assert stream.retire()["event"] == "cancelled"


def test_model_failure_closes_pcm_admission_instead_of_accepting_inert_input():
    class BrokenRecognizer(Recognizer):
        def decode_stream(self, stream):
            raise ValueError("injected native inference failure")

    with ThreadPoolExecutor(1) as owner:
        stream = NativeAsyncStream(BrokenRecognizer(), owner)
        stream.push_audio(np.ones(512))
        terminal = stream.retire()
        assert terminal["event"] == "error"
        assert "injected native inference failure" in terminal["error"]
        assert stream.text == ""
        with pytest.raises(RuntimeError, match="closed"):
            stream.push_audio(np.ones(512))
        with pytest.raises(RuntimeError, match="closed"):
            stream.finish()
