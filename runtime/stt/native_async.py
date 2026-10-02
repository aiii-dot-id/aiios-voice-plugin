"""Private native recognizer behind the existing speech-model stream interface.

One supplied executor owns native model calls. PCM/control admission stays on
the caller's lane; cancellation does not acquire an inference lock. This is an
isolated candidate, not selected by any deployed loader or public SDK change.
"""

import threading
import time
from types import SimpleNamespace

from runtime.stt.cuda_stream import PCMIngress, StreamCancelled
from runtime.stt.native_streaming import NativeInput


class NativeAsyncStream:
    def __init__(self, recognizer, executor):
        self.ingress = PCMIngress(capacity=16000 * 60)
        self._condition = threading.Condition()
        self._text = ""
        self.terminal = None
        self.partial_events = []
        self.future = executor.submit(self._run, recognizer)

    @property
    def text(self):
        with self._condition:
            return self._text

    @staticmethod
    def update(text):
        return [SimpleNamespace(result=SimpleNamespace(text=text))]

    def push_audio(self, pcm):
        self.ingress.push(pcm)
        return self.update(self.text)

    def cancel(self):
        # The ingress condition protects only copies/counters, never inference.
        # Taking the publication fence after setting the signal makes return
        # mean no later text can cross that fence for this stream.
        self.ingress.cancel()
        with self._condition:
            self._text = ""
            self._condition.notify_all()

    def finish_input(self):
        """Admit the cutoff without waiting for the inference owner."""
        self.ingress.finish()

    def finish(self):
        self.finish_input()
        result = self.future.result(timeout=20)
        if result["event"] != "final":
            raise RuntimeError("Native recognition did not complete: " + str(result))
        return self.update(result["text"])

    def retire(self, timeout=10):
        return self.future.result(timeout=timeout)

    def observe(self, after, timeout=1):
        """Wait for new observations, not for inference under a control lock.

        The returned cursor refers to the immutable bounded partial history.
        Cancellation retires unsent text; terminal still resolves separately.
        """
        with self._condition:
            if type(after) is not int or not 0 <= after <= len(self.partial_events):
                raise ValueError("Invalid native observation cursor")
            self._condition.wait_for(
                lambda: len(self.partial_events) > after or self.terminal is not None,
                timeout=timeout,
            )
            rows = (
                [] if self.ingress.cancelled.is_set() else self.partial_events[after:]
            )
            return len(self.partial_events), rows, self.terminal

    def _batches(self):
        offset = 0
        while True:
            with self.ingress._condition:
                ready = self.ingress._condition.wait_for(
                    lambda boundary=offset: (
                        self.ingress.cancelled.is_set()
                        or self.ingress.finished
                        or self.ingress.samples > boundary
                    ),
                    timeout=10,
                )
                if self.ingress.cancelled.is_set():
                    raise StreamCancelled("Native input cancelled")
                if not ready:
                    raise TimeoutError("Native PCM source stalled")
                if offset == self.ingress.samples and self.ingress.finished:
                    return
                end = min(offset + 2048, self.ingress.samples)
                values = self.ingress._pcm[offset:end].copy()
                offset = end
            yield values

    def _publish(self, text):
        with self._condition:
            if self.ingress.cancelled.is_set():
                return
            if len(self.partial_events) >= 4096:
                raise RuntimeError("Native partial observation bound exceeded")
            self._text = text
            self.partial_events.append({"text": text, "at_ns": time.perf_counter_ns()})
            self._condition.notify_all()

    def _run(self, recognizer):
        native = None
        try:
            native = NativeInput(recognizer)
            native.cancelled = self.ingress.cancelled
            for values in self._batches():
                for text in native.push(values):
                    self._publish(text)
            final = native.finish()
            if native.samples != self.ingress.samples or recognizer.is_ready(
                native.stream
            ):
                raise RuntimeError("Native input tail unresolved")
            with self._condition:
                if self.ingress.cancelled.is_set():
                    raise StreamCancelled("Native final cancelled at publication")
                self._text = final
                self.terminal = {
                    "event": "final",
                    "text": final,
                    "captured_samples": native.samples,
                    "input_finished": self.ingress.finished,
                    "model_padding_samples": native.model_padding_samples,
                    "decoder_ready_after_finish": False,
                }
                self._condition.notify_all()
        except Exception as error:  # noqa: BLE001 - every owned inference resolves
            # A failed inference cannot keep accepting PCM into an inert stream.
            # Retire input before publishing its terminal verdict, without
            # converting a genuine inference error into a cancellation.
            with self.ingress._condition:
                self.ingress.finished = True
                self.ingress._condition.notify_all()
            with self._condition:
                cancelled = self.ingress.cancelled.is_set()
                self._text = ""
                self.terminal = {
                    "event": "cancelled" if cancelled else "error",
                    "error": repr(error),
                    "text": "",
                }
                self._condition.notify_all()
        return self.terminal
