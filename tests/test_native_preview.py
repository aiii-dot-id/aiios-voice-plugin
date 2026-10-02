from types import SimpleNamespace

import pytest

from runtime.stt.native_preview import NativePreviewRecognizer


class Stream:
    def __init__(self, context):
        self.context = context
        self.features = SimpleNamespace(frames_ready=0)
        self.processed = 0
        self.tokens = []
        self.text = ""
        self.finished = False

    def accept_waveform(self, _rate, samples):
        self.features.frames_ready += len(samples)

    def set_option(self, key, value):
        self.option = key, value

    def input_finished(self):
        self.finished = True


class Recognizer:
    cache_aligned = True

    def create_stream(self, *, right_context=6):
        return Stream(right_context)

    def window(self, stream):
        stride = 8 * (stream.context + 1)
        return (
            (0, stride - 7, 0)
            if stream.processed == 0
            else (stream.processed - 16, stride + 9, 2)
        )

    def is_ready(self, stream):
        return sum(self.window(stream)[:2]) <= stream.features.frames_ready

    def decode_stream(self, stream):
        stream.processed += 8 * (stream.context + 1)
        stream.text = (
            "preview spelling"
            if stream.context == 3
            else ("" if stream.processed == 56 else "correct final spelling")
        )
        if stream.text:
            stream.tokens.append(stream.context)

    def get_result(self, stream):
        return stream.text


def test_features_shared_caches_independent_preview_does_not_double_admit():
    r = NativePreviewRecognizer(Recognizer())
    s = r.create_stream()
    s.accept_waveform(16000, list(range(25)))
    s.set_option("language", "en-US")
    assert s.features.frames_ready == 25
    assert s.preview.features is s.confirm.features
    assert s.preview.tokens is not s.confirm.tokens
    assert s.preview.option == s.confirm.option == ("language", "en-US")
    r.decode_stream(s)
    assert r.get_result(s) == "preview spelling"
    assert s.tokens == []
    assert s.confirm_decodes == 0


def test_confirmation_replaces_preview_and_stops_extra_compute():
    r = NativePreviewRecognizer(Recognizer())
    s = r.create_stream()
    s.accept_waveform(16000, [0] * 300)
    order = []
    while r.is_ready(s):
        order.append(r.window(s))
        r.decode_stream(s)
    assert order[:5] == [(0, 25, 0), (0, 49, 0), (16, 41, 2), (48, 41, 2), (40, 65, 2)]
    assert s.handoff_frames == 112
    assert s.preview_decodes == 3
    assert s.confirm_decodes == 5
    assert r.get_result(s) == "correct final spelling"
    assert s.tokens == [6] * 4


def test_finish_never_promotes_preview_when_confirmation_empty():
    r = NativePreviewRecognizer(Recognizer())
    s = r.create_stream()
    s.accept_waveform(16000, [0] * 25)
    r.decode_stream(s)
    assert r.get_result(s) == "preview spelling"
    s.input_finished()
    assert r.get_result(s) == ""
    assert s.tokens == []
    assert s.confirm.finished
    assert not r.is_ready(s)


def test_streams_do_not_share_input_or_decoder_state():
    r = NativePreviewRecognizer(Recognizer())
    a, b = r.create_stream(), r.create_stream()
    a.accept_waveform(16000, [0] * 25)
    r.decode_stream(a)
    assert b.features.frames_ready == 0
    assert r.get_result(b) == ""
    with pytest.raises(ValueError, match="ready"):
        r.decode_stream(b)


def test_uncorrected_graph_refused():
    inner = Recognizer()
    inner.cache_aligned = False
    with pytest.raises(ValueError, match="cache-aligned"):
        NativePreviewRecognizer(inner)


def test_separate_preview_executor_keeps_final_owner():
    confirm, preview = Recognizer(), Recognizer()
    confirm.metadata = preview.metadata = {"checkpoint_geometry": "same"}
    confirm.symbols = preview.symbols = {0: "token"}
    calls = []
    original = preview.decode_stream

    def record(stream):
        calls.append(stream.context)
        original(stream)

    preview.decode_stream = record
    r = NativePreviewRecognizer(confirm, preview_recognizer=preview)
    s = r.create_stream()
    s.accept_waveform(16000, [0] * 300)
    while r.is_ready(s):
        r.decode_stream(s)
    assert calls == [3, 3, 3]
    assert r.get_result(s) == "correct final spelling"
    assert s.confirm_decodes == 5
    preview.symbols = {0: "different"}
    with pytest.raises(ValueError, match="vocabulary"):
        NativePreviewRecognizer(confirm, preview_recognizer=preview)
