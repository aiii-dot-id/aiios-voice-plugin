"""Opt-in 320ms preview with the unchanged 560ms final recognizer as authority.

One model owner and one feature stream; decoder caches are independent. A
separately supplied preview executor must be bound to the same checkpoint by
the owner. No new defaults or worker controls. The caller must qualify compute
and memory on its target before selecting this adapter.
"""


class PreviewStream:
    def __init__(self, recognizer, preview_recognizer):
        self.confirm = recognizer.create_stream()
        self.preview = preview_recognizer.create_stream(right_context=3)
        self.preview.features = self.confirm.features
        self.preview_retired = False
        self.handoff_frames = None
        self.preview_decodes = 0
        self.confirm_decodes = 0

    @property
    def tokens(self):
        return self.confirm.tokens

    @property
    def features(self):
        return self.confirm.features

    def accept_waveform(self, rate, samples):
        self.confirm.accept_waveform(rate, samples)

    def set_option(self, key, value):
        self.confirm.set_option(key, value)
        self.preview.set_option(key, value)

    def input_finished(self):
        # Model-only finish padding must never invent a last preview. Completion
        # and its coverage always come from the confirmation stream.
        self.preview_retired = True
        self.confirm.input_finished()


class NativePreviewRecognizer:
    def __init__(self, recognizer, *, preview_recognizer=None):
        preview_recognizer = (
            recognizer if preview_recognizer is None else preview_recognizer
        )
        if not recognizer.cache_aligned or not preview_recognizer.cache_aligned:
            raise ValueError("Preview requires the cache-aligned graph")
        if preview_recognizer is not recognizer and (
            recognizer.metadata != preview_recognizer.metadata
            or recognizer.symbols != preview_recognizer.symbols
        ):
            raise ValueError("Preview execution geometry or vocabulary differs")
        self.inner = recognizer
        self.preview_inner = preview_recognizer

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def create_stream(self):
        return PreviewStream(self.inner, self.preview_inner)

    def _owner(self, stream, selected):
        return self.inner if selected is stream.confirm else self.preview_inner

    def _next(self, stream):
        choices = [stream.confirm]
        if not stream.preview_retired:
            choices.append(stream.preview)
        ready = [s for s in choices if self._owner(stream, s).is_ready(s)]
        if not ready:
            return None
        # Consume by acoustic horizon. Confirmation wins a tie and may retire
        # preview before spending another encoder call on it.
        return min(ready, key=lambda s: sum(self._owner(stream, s).window(s)[:2]))

    def is_ready(self, stream):
        return self._next(stream) is not None

    def window(self, stream):
        selected = self._next(stream)
        if selected is None:
            raise ValueError("No ready preview or confirmation window")
        return self._owner(stream, selected).window(selected)

    def decode_stream(self, stream):
        selected = self._next(stream)
        if selected is None:
            raise ValueError("No ready preview or confirmation window")
        self._owner(stream, selected).decode_stream(selected)
        if selected is stream.confirm:
            stream.confirm_decodes += 1
            if not stream.preview_retired and self.inner.get_result(selected).strip():
                stream.preview_retired = True
                stream.handoff_frames = selected.processed
        else:
            stream.preview_decodes += 1

    def get_result(self, stream):
        selected = stream.confirm if stream.preview_retired else stream.preview
        return self._owner(stream, selected).get_result(selected)
