import base64
import io
import json
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from runtime.stt.native_resident import NativeResident, serve, terminal_record
from tests.test_native_streaming import Recognizer, Stream


class RecognizerFixture(Recognizer):
    def create_stream(self):
        self.stream = Stream()
        self.stream.tokens = [1, 2, 3]
        self.decoded = 0
        return self.stream

    def window(self, stream):
        return 0, 65, 2


def pcm(sid, start=0):
    return {
        "op": "pcm",
        "sid": sid,
        "start": start,
        "pcm": base64.b64encode(np.ones(512, dtype="<f4").tobytes()).decode(),
    }


def wait_terminal(resident):
    for watcher in resident.watchers:
        watcher.join(2)
        assert not watcher.is_alive()
    assert resident.active.terminal
    assert resident.fault is None


def rows(output):
    return [json.loads(line[6:]) for line in output.getvalue().splitlines()]


def test_finish_cancel_and_recovery_use_the_existing_private_contract():
    recognizer = RecognizerFixture()
    recognizer.hold = True
    output = io.BytesIO()
    resident = NativeResident(recognizer, output)
    try:
        resident.dispatch({"op": "start", "sid": "first"})
        resident.dispatch(pcm("first"))
        assert recognizer.entered.wait(1)
        # No wall-clock guess: inference remains positively held after BOTH controls.
        resident.dispatch({"op": "finish", "sid": "first"})
        assert not resident.active.stream.future.done()
        resident.dispatch({"op": "cancel", "sid": "first"})
        assert not resident.active.stream.future.done()
        recognizer.release.set()
        wait_terminal(resident)
        recognizer.hold = False
        resident.dispatch({"op": "start", "sid": "recovery"})
        resident.dispatch(pcm("recovery"))
        resident.dispatch({"op": "finish", "sid": "recovery"})
        wait_terminal(resident)
        assert resident.dispatch({"op": "close"}) is False
    finally:
        recognizer.release.set()
        resident.close()
    observed = rows(output)
    finals = [r for r in observed if r["event"] in {"final", "cancelled", "error"}]
    assert [(r["sid"], r["event"]) for r in finals] == [
        ("first", "cancelled"),
        ("recovery", "final"),
    ]
    assert finals[-1]["text"] == "opening words complete"
    assert finals[-1]["completion"]["features_exhausted"]
    assert finals[-1]["admitted"] == 512
    assert finals[-1]["completion"]["covered_source_end"] >= 512
    assert "peak_allocated_bytes" not in finals[-1]
    cancel_at = next(
        i for i, r in enumerate(observed) if r["event"] == "cancel_admitted"
    )
    assert not any(
        r["sid"] == "first" and r["event"] == "partial" for r in observed[cancel_at:]
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"op": "pcm", "sid": "foreign", "start": 0},
        {"op": "start", "sid": "first"},
        {"op": "start", "sid": "second"},
        {"op": "close"},
        {"op": "inference", "sid": "first"},
    ],
)
def test_unknown_overlapping_and_early_close_refused(bad):
    resident = NativeResident(RecognizerFixture(), io.BytesIO())
    try:
        resident.dispatch({"op": "start", "sid": "first"})
        with pytest.raises(ValueError):
            resident.dispatch(bad)
    finally:
        resident.close()


def test_sample_gap_duplicate_finish_and_post_cutoff_pcm_refused():
    recognizer = RecognizerFixture()
    recognizer.hold = True
    resident = NativeResident(recognizer, io.BytesIO())
    try:
        resident.dispatch({"op": "start", "sid": "s"})
        with pytest.raises(ValueError, match="sample gap"):
            resident.dispatch(pcm("s", 1))
        resident.dispatch(pcm("s"))
        assert recognizer.entered.wait(1)
        resident.dispatch({"op": "finish", "sid": "s"})
        for bad in ({"op": "finish", "sid": "s"}, pcm("s", 512)):
            with pytest.raises(RuntimeError, match="closed"):
                resident.dispatch(bad)
    finally:
        recognizer.release.set()
        resident.close()


def test_slow_output_does_not_serialize_cancellation():
    class SlowOutput(io.BytesIO):
        entered = threading.Event()
        released = threading.Event()

        def write(self, data):
            self.entered.set()
            assert self.released.wait(2)
            return super().write(data)

    output = SlowOutput()
    recognizer = RecognizerFixture()
    recognizer.hold = True
    resident = NativeResident(recognizer, output)
    try:
        resident.dispatch({"op": "start", "sid": "s"})
        assert output.entered.wait(1)
        resident.dispatch(pcm("s"))
        assert recognizer.entered.wait(1)
        returned = threading.Event()

        def cancel():
            resident.dispatch({"op": "cancel", "sid": "s"})
            returned.set()

        caller = threading.Thread(target=cancel)
        caller.start()
        assert returned.wait(0.5), "Cancellation waited behind output/inference"
        caller.join(1)
        assert not recognizer.release.is_set() and not output.released.is_set()
    finally:
        recognizer.release.set()
        output.released.set()
        resident.close()


def test_native_completion_cannot_invent_a_consumed_tail():
    resident = NativeResident(RecognizerFixture(), io.BytesIO())
    try:
        resident.dispatch({"op": "start", "sid": "s"})
        resident.dispatch(pcm("s"))
        resident.dispatch({"op": "finish", "sid": "s"})
        wait_terminal(resident)
        record = resident.active
        native = record.stream.terminal
        assert terminal_record(record, native)["event"] == "final"
        record.observed.spans.clear()
        assert terminal_record(record, native)["event"] == "error"
    finally:
        resident.close()


def test_failed_output_is_not_a_quiet_success():
    class BrokenOutput:
        def write(self, _):
            raise OSError("injected broken pipe")

    resident = NativeResident(RecognizerFixture(), BrokenOutput())
    resident.emit("ready")
    assert resident.failed.wait(1)
    with pytest.raises(RuntimeError, match="observation failure"):
        resident.close()


def test_graceful_close_does_not_leave_a_blocked_input_reader():
    class CloseOnly:
        reads = 0

        def readline(self, size):
            self.reads += 1
            if self.reads != 1:
                raise AssertionError("Read again after explicit close")
            return b'{"op":"close"}\n'

    source = CloseOnly()
    serve(RecognizerFixture(), source, io.BytesIO(), {"backend": "fixture"})
    assert source.reads == 1


def test_observed_inference_uses_high_resolution_counter(monkeypatch):
    from runtime.stt import native_resident as module

    ticks = iter((100, 103))
    monkeypatch.setattr(
        module, "time", SimpleNamespace(perf_counter_ns=lambda: next(ticks))
    )
    observations = []
    recognizer = RecognizerFixture()
    observed = module.ObservedRecognizer(
        recognizer, lambda event, **fields: observations.append(fields), "s"
    )
    stream = observed.create_stream()
    observed.decode_stream(stream)
    assert observations[0]["decode_start_ns"] == 100
    assert observed.spans[0]["decode_end_ns"] == 103
