"""Input half-close completion is not inferred from a transcript or RPC timing."""

import asyncio

import numpy as np
import pytest

from runtime.plugin_engine.audio import END, PCM, Frame
from runtime.plugin_engine.session import Recognition, Refused
from tests.plugin_models import open_args
from tests.test_plugin_engine import parts as _engine_parts
from tests.test_plugin_engine import until

parts = _engine_parts


def finish(e, end):
    return e.admit(
        "speech.session.finish_input",
        {
            "session_id": e.id,
            "stream_id": "mic",
            "end_sample": end,
        },
    )


def completion(e, events, end):
    found = [x for x in events if x["type"] == "input_finished"]
    assert len(found) == 1
    event = found[0]
    assert event["session_id"] == e.id
    assert event["stream_id"] == "mic"
    assert event["end_sample"] == event["processed_end_sample"] == end
    s = e.status()
    assert s["input"]["state"] == "finished"
    assert not s["recognition"]["finalization_pending"]
    assert s["input_completion"] == {
        k: event[k]
        for k in ("stream_id", "end_sample", "processed_end_sample", "sequence")
    }
    assert s["state_sequence"] >= event["sequence"]
    assert all(
        x["sequence"] < event["sequence"]
        for x in events
        if x["type"] == "transcript_final"
    )
    # Status is a snapshot, never a mutable alias to the owner.
    s["input_completion"]["end_sample"] = -1
    assert e.status()["input_completion"]["end_sample"] == end
    return event


@pytest.mark.asyncio
@pytest.mark.parametrize("samples", [0, 1037])
async def test_silent_finish_emits_completion_without_a_fabricated_transcript(
    parts, samples
):
    e, _, events, _ = parts
    try:
        e.admit("speech.session.open", open_args())
        await until(lambda: e.lifecycle == "open")
        before = e.status()["state_sequence"]
        assert e.status()["input_completion"] is None
        if samples:
            e.feed(Frame(PCM, 7, 1, 0, b"\0\0" * samples))
        finish(e, samples)
        e.feed(Frame(END, 7, 2 if samples else 1, samples))
        await until(e.input_done.is_set)
        event = completion(e, events, samples)
        assert event["sequence"] > before
        assert not any(x["type"] == "transcript_final" for x in events)
        assert e.lifecycle == "open", "recognition completion is not session close"
        # Retry after completion cannot re-arm input or mint another event.
        stable = e.status()
        finish(e, samples)
        assert e.status() == stable
        with pytest.raises(Refused, match="INPUT_CUTOFF"):
            finish(e, samples + 1)
        # A final host-authored reply is still admissible after input finishes.
        e.admit(
            "speech.session.synthesize",
            {
                "session_id": e.id,
                "synthesis_id": "final-reply",
                "text": "A complete final reply.",
            },
        )
        await until(lambda: e.current.terminal)
        assert e.lifecycle == "open"
    finally:
        await e.shutdown()


@pytest.mark.asyncio
async def test_completion_waits_for_exact_nonfull_tail_and_follows_real_final(parts):
    e, model, events, _ = parts
    try:
        e.admit("speech.session.open", open_args())
        await until(lambda: e.lifecycle == "open")
        signal = np.full(1037, 12000, dtype="<i2")
        finish(e, len(signal))
        e.feed(Frame(PCM, 7, 1, 0, signal[:900].tobytes()))
        await asyncio.sleep(0)
        assert e.status()["input_completion"] is None
        e.feed(Frame(PCM, 7, 2, 900, signal[900:].tobytes()))
        await until(e.input_done.is_set)
        event = completion(e, events, len(signal))
        final = [x for x in events if x["type"] == "transcript_final"][-1]
        assert final["text"] == "cobalt lantern seventeen"
        assert final["sequence"] < event["sequence"]
        np.testing.assert_array_equal(
            np.asarray(model.samples)[:1037], signal.astype(np.float32) / 32768
        )
    finally:
        await e.shutdown()


@pytest.mark.asyncio
async def test_abort_before_pending_tail_never_claims_input_completed(parts):
    e, _, events, _ = parts
    try:
        e.admit("speech.session.open", open_args())
        await until(lambda: e.lifecycle == "open")
        finish(e, 1037)
        e.admit("speech.session.close", {"session_id": e.id, "mode": "abort"})
        await until(lambda: e.lifecycle == "closed")
        assert not any(x["type"] == "input_finished" for x in events)
        assert e.status()["input_completion"] is None
    finally:
        await e.shutdown()


@pytest.mark.asyncio
async def test_final_transcript_cannot_stand_in_for_recognizer_retirement(
    parts, monkeypatch
):
    e, _, events, _ = parts
    retiring, release = asyncio.Event(), asyncio.Event()
    original = Recognition.run

    async def delayed(self):
        await original(self)
        retiring.set()
        await release.wait()

    monkeypatch.setattr(Recognition, "run", delayed)
    try:
        e.admit("speech.session.open", open_args())
        await until(lambda: e.lifecycle == "open")
        finish(e, 1037)
        e.feed(Frame(PCM, 7, 1, 0, np.full(1037, 12000, dtype="<i2").tobytes()))
        await until(retiring.is_set)
        assert any(x["type"] == "transcript_final" for x in events)
        assert e.status()["input"]["state"] == "finishing"
        assert e.status()["recognition"]["finalization_pending"]
        assert e.status()["input_completion"] is None
        assert not any(x["type"] == "input_finished" for x in events)
        release.set()
        await until(e.input_done.is_set)
        completion(e, events, 1037)
    finally:
        release.set()
        await e.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("utterances", [1, 2])
async def test_finish_after_already_finalized_utterances_needs_no_new_transcript(
    parts, utterances
):
    e, _, events, _ = parts
    try:
        e.admit("speech.session.open", open_args())
        await until(lambda: e.lifecycle == "open")
        turn = np.concatenate(
            (np.full(1024, 12000, dtype="<i2"), np.zeros(10752, dtype="<i2"))
        )
        signal = np.tile(turn, utterances)
        e.feed(Frame(PCM, 7, 1, 0, signal.tobytes()))
        await until(
            lambda: (
                len([x for x in events if x["type"] == "transcript_final"])
                == utterances
            )
        )
        assert e.status()["input_completion"] is None
        finish(e, len(signal))
        await until(e.input_done.is_set)
        completion(e, events, len(signal))
        assert len([x for x in events if x["type"] == "transcript_final"]) == utterances
    finally:
        await e.shutdown()
