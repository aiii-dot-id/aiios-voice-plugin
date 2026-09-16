import asyncio
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np
import pytest

from runtime.plugin_engine.audio import END, PCM, Frame, read_frame
from runtime.plugin_engine.mlx import ResidentMLXModels
from runtime.plugin_engine.session import Refused, ResidentEngine
from runtime.speech_output.mlx_backend import MLXTTSBackend
from scripts.build_plugin_carrier import SDK_SOURCE
from tests.plugin_models import Models, open_args


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.002)


@pytest.fixture
def parts():
    models, events, frames = Models(), [], []
    executor, control = ThreadPoolExecutor(1), ThreadPoolExecutor(1)

    async def write(frame, generation):
        if frame.kind == END or not generation.fenced:
            frames.append(frame)

    engine = ResidentEngine(
        models,
        executor,
        control,
        events.append,
        write,
        tail_timeout=0.15,
        drain_timeout=0.15,
    )
    yield engine, models, events, frames
    models.release.set()
    executor.shutdown(wait=True)
    control.shutdown(wait=True)


def test_sdk_audio_vectors_and_truncation():
    source = SDK_SOURCE / "vectors/audio_framing.json"
    data = json.loads(source.read_text())
    encoded = bytes.fromhex(data["hex"])
    reader = io.BytesIO(encoded)
    frames = [read_frame(reader) for _ in data["frames"]]
    assert b"".join(f.encode() for f in frames) == encoded
    assert read_frame(reader) is None
    with pytest.raises(EOFError):
        read_frame(io.BytesIO(encoded[:29]))
    with pytest.raises(ValueError):
        Frame(PCM, 1, 0, -1, b"\0\0").encode()
    with pytest.raises(ValueError):
        Frame(END, 1, 0, 0, b"\0\0").encode()


def test_mlx_adapter_uses_output_service_contract_without_second_model():
    model = object.__new__(ResidentMLXModels)
    captured = []
    model.mx = SimpleNamespace(random=SimpleNamespace(seed=lambda value: None))
    model.tts = SimpleNamespace(generate=lambda **kw: captured.append(kw))
    model.tts_stream("Complete reply.")
    assert model.max_tokens == MLXTTSBackend.max_tokens
    assert captured[0]["max_tokens"] == model.max_tokens
    assert captured[0]["text"] == "Complete reply."
    assert model.model is model.tts
    assert model.tts_next(iter([])) is None


@pytest.mark.asyncio
async def test_failure_remains_terminal_and_shutdown_releases_owned_tasks(parts):
    e, _, events, _ = parts
    try:
        e.admit("speech.session.open", open_args())
        await until(lambda: e.lifecycle == "open")
        e.fail(RuntimeError("injected input failure"))
        e.emit("transcript_final", text="must never appear")
        await until(lambda: e.lifecycle == "failed")
        assert events[-1]["type"] == "failure"
        await e.shutdown()
        assert not e.tasks
    finally:
        await e.shutdown()


@pytest.mark.asyncio
async def test_session_end_allows_immediate_reuse_without_callback_delay(parts):
    e, _, events, _ = parts
    observed = []

    def observe(event):
        events.append(event)
        if event["type"] == "session_end" and len(observed) == 0:
            row = {"unretired_tasks": len(e.tasks)}
            observed.append(row)
            try:
                row["reopen"] = e.admit(
                    "speech.session.open",
                    dict(open_args(), session_id="immediate-reuse"),
                )
            except Refused as error:
                row["refusal"] = str(error)

    e.emit_sink = observe
    try:
        e.admit("speech.session.open", open_args())
        await until(lambda: e.lifecycle == "open")
        e.admit(
            "speech.session.finish_input",
            {"session_id": e.id, "stream_id": "mic", "end_sample": 0},
        )
        await until(e.input_done.is_set)
        e.admit("speech.session.close", {"session_id": e.id, "mode": "drain"})
        await until(lambda: bool(observed))
        assert observed[0]["unretired_tasks"] == 0, (
            "session_end preceded actual task retirement"
        )
        assert "refusal" not in observed[0], "session_end could not be followed by open"
        assert observed[0]["reopen"]["accepted"]
        await until(lambda: e.lifecycle == "open")
        e.admit("speech.session.close", {"session_id": e.id, "mode": "abort"})
        await until(lambda: e.lifecycle == "closed" and not e.tasks)
    finally:
        await e.shutdown()


@pytest.mark.asyncio
async def test_abort_admits_without_waiting_but_terminal_waits_for_model(parts):
    e, model, events, _ = parts
    try:
        e.admit("speech.session.open", open_args())
        await until(lambda: e.lifecycle == "open")
        model.stall = True
        e.admit(
            "speech.session.synthesize",
            {"session_id": e.id, "synthesis_id": "s1", "text": "In-flight reply."},
        )
        await until(model.blocked.is_set)
        before = time.perf_counter()
        e.admit("speech.session.close", {"session_id": e.id, "mode": "abort"})
        assert time.perf_counter() - before < 0.05
        await asyncio.sleep(0.01)
        assert not any(x["type"] == "session_end" for x in events)
        model.release.set()
        await until(lambda: e.lifecycle == "closed")
        await until(lambda: not e.tasks)
        assert events[-1]["type"] == "session_end"
        assert events[-1]["playback_verified"] is False
    finally:
        await e.shutdown()


@pytest.mark.asyncio
async def test_exact_input_tail_and_opening_words(parts):
    e, model, events, _ = parts
    try:
        result = e.admit("speech.session.open", open_args())
        assert result["audio"]["input"]["rate"] == 16000
        assert result["audio"]["output"]["rate"] == 24000
        await until(lambda: e.lifecycle == "open")
        signal = np.concatenate(
            [np.arange(900, dtype=np.int16), np.full(137, 12000, np.int16)]
        )
        e.admit(
            "speech.session.finish_input",
            {"session_id": e.id, "stream_id": "mic", "end_sample": len(signal)},
        )
        e.feed(Frame(PCM, 7, 1, 0, signal[:900].astype("<i2").tobytes()))
        assert not e.input_done.is_set()
        e.feed(Frame(PCM, 7, 2, 900, signal[900:].astype("<i2").tobytes()))
        await until(e.input_done.is_set)
        np.testing.assert_array_equal(
            np.asarray(model.samples)[: len(signal)], signal.astype(np.float32) / 32768
        )
        final = [x for x in events if x["type"] == "transcript_final"][-1]
        assert final["end_sample"] == 1037
        assert final["text"] == "cobalt lantern seventeen"
        assert e.padding == 499
        assert e.status()["input"]["processed_end_sample"] == 1037
        e.admit("speech.session.close", {"session_id": e.id, "mode": "drain"})
        await until(lambda: e.lifecycle == "closed")
    finally:
        await e.shutdown()


@pytest.mark.asyncio
async def test_cancel_and_status_do_not_wait_behind_inference(parts):
    e, model, events, frames = parts
    try:
        e.admit("speech.session.open", open_args())
        await until(lambda: e.lifecycle == "open")
        model.stall = True
        e.admit(
            "speech.session.synthesize",
            {"session_id": e.id, "synthesis_id": "s1", "text": "First reply."},
        )
        await until(model.blocked.is_set)
        begun = time.perf_counter()
        e.admit(
            "speech.session.stop_playback", {"session_id": e.id, "synthesis_id": "s1"}
        )
        e.admit(
            "speech.session.cancel_synthesis",
            {"session_id": e.id, "synthesis_id": "s1"},
        )
        snapshot = e.admit("speech.session.status", {"session_id": e.id})
        assert time.perf_counter() - begun < 0.05
        assert not e.current.terminal
        assert snapshot["playback"]["state"] == "unobserved"
        model.release.set()
        await until(lambda: e.current.terminal)
        assert not any(f.kind == PCM for f in frames)
        assert len([x for x in events if x["type"] == "synthesis_cancelled"]) == 1
        model.stall = False
        e.admit(
            "speech.session.synthesize",
            {"session_id": e.id, "synthesis_id": "s2", "text": "Recovery reply."},
        )
        await until(lambda: e.current.terminal)
        assert e.current.delivered == 1337
        assert [f for f in frames if f.kind == END][-1].start == 1337
        with pytest.raises(Refused, match="ID_REUSE"):
            e.admit(
                "speech.session.synthesize",
                {"session_id": e.id, "synthesis_id": "s1", "text": "stale"},
            )
    finally:
        await e.shutdown()


@pytest.mark.asyncio
async def test_missing_render_report_cannot_certify_drain(parts):
    e, _, events, _ = parts
    try:
        e.admit("speech.session.open", open_args())
        await until(lambda: e.lifecycle == "open")
        e.admit(
            "speech.session.synthesize",
            {"session_id": e.id, "synthesis_id": "s1", "text": "The full tail."},
        )
        await until(lambda: e.current.terminal)
        e.admit(
            "speech.session.finish_input",
            {"session_id": e.id, "stream_id": "mic", "end_sample": 0},
        )
        e.admit("speech.session.close", {"session_id": e.id, "mode": "drain"})
        await until(lambda: e.lifecycle == "failed")
        assert not any(x["type"] == "session_end" for x in events)
        assert e.status()["playback"]["state"] == "unobserved"
    finally:
        await e.shutdown()


@pytest.mark.asyncio
async def test_valid_host_render_report_and_malformed_report(parts):
    e, _, events, _ = parts
    try:
        e.admit("speech.session.open", open_args())
        await until(lambda: e.lifecycle == "open")
        e.admit(
            "speech.session.synthesize",
            {"session_id": e.id, "synthesis_id": "s1", "text": "A complete tail."},
        )
        await until(lambda: e.current.terminal)
        with pytest.raises(Refused, match="PLAYBACK_REPORT"):
            e.playback_report(
                "s1", e.current.stream, 1336, session_id=e.id, terminal=True
            )
        with pytest.raises(Refused, match="PLAYBACK_REPORT"):
            e.playback_report("s1", 999, 1337, session_id=e.id, terminal=True)
        e.playback_report("s1", e.current.stream, 1337, session_id=e.id, terminal=True)
        e.admit(
            "speech.session.finish_input",
            {"session_id": e.id, "stream_id": "mic", "end_sample": 0},
        )
        e.admit("speech.session.close", {"session_id": e.id, "mode": "drain"})
        await until(lambda: e.lifecycle == "closed")
        assert [x["sequence"] for x in events] == list(range(1, len(events) + 1))
    finally:
        await e.shutdown()


@pytest.mark.asyncio
async def test_bad_input_and_missing_tail_are_not_silence(parts):
    e, _, events, _ = parts
    try:
        e.admit("speech.session.open", open_args())
        await until(lambda: e.lifecycle == "open")
        with pytest.raises(Refused, match="INPUT_SPAN"):
            e.feed(Frame(PCM, 7, 1, 3, b"\0\0"))
        e.admit(
            "speech.session.finish_input",
            {"session_id": e.id, "stream_id": "mic", "end_sample": 100},
        )
        e.feed(Frame(PCM, 7, 1, 0, b"\0\0" * 50))
        with pytest.raises(Refused, match="INPUT_TAIL"):
            e.feed(Frame(END, 7, 2, 50))
        await until(lambda: e.lifecycle == "failed")
        assert not any(x["type"] == "transcript_final" for x in events)
    finally:
        await e.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["failed", "closed"])
async def test_finish_cannot_admit_new_boundary_after_terminal(parts, terminal):
    e, _, _, _ = parts
    try:
        e.admit("speech.session.open", open_args())
        await until(lambda: e.lifecycle == "open")
        if terminal == "failed":
            e.fail(RuntimeError("injected failure"))
            await until(lambda: e.lifecycle == "failed")
        else:
            e.admit("speech.session.close", {"session_id": e.id, "mode": "abort"})
            await until(lambda: e.lifecycle == "closed")
        with pytest.raises(Refused, match="SESSION_STATE"):
            e.admit(
                "speech.session.finish_input",
                {"session_id": e.id, "stream_id": "mic", "end_sample": 0},
            )
        assert e.cutoff is None
    finally:
        await e.shutdown()


@pytest.mark.asyncio
async def test_failure_cannot_release_host_binding_before_inference_retires(parts):
    e, model, events, _ = parts
    try:
        e.admit("speech.session.open", open_args())
        await until(lambda: e.lifecycle == "open")
        model.stall = True
        e.admit(
            "speech.session.synthesize",
            {"session_id": e.id, "synthesis_id": "s1", "text": "In flight."},
        )
        await until(model.blocked.is_set)
        e.fail(RuntimeError("injected endpoint failure"))
        await asyncio.sleep(0.01)
        assert e.status()["lifecycle"] == "draining"
        assert not any(x["type"] in ("failure", "session_end") for x in events)
        model.release.set()
        await until(lambda: e.lifecycle == "failed")
        assert e.current.job.finished
        assert events[-1]["resources_released"] is True
        assert events[-1]["playback_verified"] is False
    finally:
        model.release.set()
        await e.shutdown()


@pytest.mark.asyncio
async def test_failure_waits_for_unknown_inflight_audio_write(parts):
    e, _, events, _ = parts
    released = asyncio.Event()
    e.retire_audio = released.wait
    try:
        e.admit("speech.session.open", open_args())
        await until(lambda: e.lifecycle == "open")
        e.fail(RuntimeError("write outcome unknown"))
        await asyncio.sleep(0.02)
        assert e.lifecycle == "draining"
        assert not any(x["type"] == "failure" for x in events)
        released.set()
        await until(lambda: e.lifecycle == "failed")
        assert events[-1]["resources_released"] is True
    finally:
        released.set()
        await e.shutdown()
