"""Engine-side playback evidence; no browser or physical-render claims."""

import asyncio
import inspect

import pytest

from runtime.plugin_engine.session import Refused
from tests.plugin_models import open_args
from tests.test_plugin_engine import parts as _engine_parts
from tests.test_plugin_engine import until

parts = _engine_parts


async def completed(parts):
    e, _, _, _ = parts
    e.admit("speech.session.open", open_args())
    await until(lambda: e.lifecycle == "open")
    e.admit(
        "speech.session.synthesize",
        {"session_id": e.id, "synthesis_id": "s1", "text": "Complete tail."},
    )
    await until(lambda: e.current.terminal)
    return e


def observe(e, rendered, *, terminal=False, stream=None, **kwargs):
    # Keep these defect reproducers executable against the old signature too.
    # The old body must fail the accounting assertion, not a new keyword error.
    if "session_id" in inspect.signature(e.playback_report).parameters:
        kwargs.setdefault("session_id", e.id)
    return e.playback_report(
        "s1",
        e.current.stream if stream is None else stream,
        rendered,
        terminal=terminal,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_terminal_playback_observation_cannot_be_reopened(parts):
    e = await completed(parts)
    try:
        observe(e, 1337, terminal=True)
        before = e.status()
        with pytest.raises(Refused, match="PLAYBACK_RESOLVED"):
            observe(e, 1337)
        assert e.status() == before
        observe(e, 1337, terminal=True)  # exact terminal retry is idempotent
        assert e.status() == before
    finally:
        await e.shutdown()


@pytest.mark.asyncio
async def test_stopped_tail_is_discarded_not_still_queued(parts):
    e = await completed(parts)
    try:
        e.admit(
            "speech.session.stop_playback", {"session_id": e.id, "synthesis_id": "s1"}
        )
        observe(e, 100, terminal=True)
        state = e.status()["playback"]
        assert state["queued_samples"] == 0, "stopped tail cannot remain queued"
        assert state["discarded_samples"] == 1237
        assert state["rendered_samples"] == 100
        assert state["delivered_samples"] == 1337
        assert state["state"] == "idle"
    finally:
        await e.shutdown()


@pytest.mark.asyncio
async def test_boolean_stream_cannot_impersonate_stream_one(parts):
    e = await completed(parts)
    try:
        assert e.current.stream == 1
        with pytest.raises(Refused, match="PLAYBACK_REPORT"):
            observe(e, 1337, stream=True, terminal=True)
        assert not e.current.playback_resolved
    finally:
        await e.shutdown()


@pytest.mark.asyncio
async def test_render_progress_advances_observable_state_watermark(parts):
    e = await completed(parts)
    try:
        before = e.status()["state_sequence"]
        observe(e, 100)
        assert e.status()["state_sequence"] > before, (
            "changed queue needs a fresh watermark"
        )
        assert parts[2][-1]["type"] == "playback_observation"
        assert parts[2][-1]["playback_verified"] is False
    finally:
        await e.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [1, "true", None])
async def test_terminal_requires_a_boolean_not_truthy_data(parts, terminal):
    e = await completed(parts)
    try:
        before = e.status()
        with pytest.raises(Refused, match="PLAYBACK_REPORT"):
            observe(e, 1337, terminal=terminal)
        assert e.status() == before
    finally:
        await e.shutdown()


@pytest.mark.asyncio
async def test_foreign_session_and_post_close_reports_cannot_mutate_state(parts):
    e = await completed(parts)
    try:
        before = e.status()
        with pytest.raises(Refused, match="STALE_SESSION"):
            observe(e, 1337, terminal=True, session_id="another-session")
        assert e.status() == before
        e.admit("speech.session.close", {"session_id": e.id, "mode": "abort"})
        await until(lambda: e.lifecycle == "closed")
        before = e.status()
        with pytest.raises(Refused, match="STALE_SESSION"):
            observe(e, 1337, terminal=True)
        assert e.status() == before
    finally:
        await e.shutdown()


@pytest.mark.asyncio
async def test_stopping_one_stream_does_not_resolve_another_unobserved_reply(parts):
    e = await completed(parts)
    try:
        e.admit(
            "speech.session.synthesize",
            {"session_id": e.id, "synthesis_id": "s2", "text": "Next reply."},
        )
        await until(lambda: e.current.terminal)
        e.admit(
            "speech.session.stop_playback", {"session_id": e.id, "synthesis_id": "s2"}
        )
        e.playback_report("s2", e.current.stream, 100, session_id=e.id, terminal=True)
        state = e.status()["playback"]
        assert state["state"] == "unobserved"
        assert state["queued_samples"] == 1337
        assert state["rendered_samples"] == 100
        assert state["discarded_samples"] == 1237
        assert state["delivered_samples"] == 2674
    finally:
        await e.shutdown()


@pytest.mark.asyncio
async def test_stop_receipt_before_inflight_write_returns_keeps_late_bytes_discarded(
    parts,
):
    e, _, _, frames = parts
    entered, release = asyncio.Event(), asyncio.Event()

    async def write(frame, generation):
        if frame.kind == 1 and not entered.is_set():
            entered.set()
            await release.wait()
        frames.append(frame)
        return True

    e.write_audio = write
    try:
        e.admit("speech.session.open", open_args())
        await until(lambda: e.lifecycle == "open")
        e.admit(
            "speech.session.synthesize",
            {"session_id": e.id, "synthesis_id": "s1", "text": "In flight."},
        )
        await entered.wait()
        e.admit(
            "speech.session.stop_playback", {"session_id": e.id, "synthesis_id": "s1"}
        )
        observe(e, 0, terminal=True)
        assert e.status()["playback"]["queued_samples"] == 0
        release.set()
        await until(lambda: e.current.terminal)
        state = e.status()["playback"]
        assert state["queued_samples"] == state["rendered_samples"] == 0
        assert state["delivered_samples"] == state["discarded_samples"] == 1200
        assert e.current.playback_resolved
    finally:
        release.set()
        await e.shutdown()


@pytest.mark.asyncio
async def test_stop_request_without_client_evidence_cannot_satisfy_drain(parts):
    e = await completed(parts)
    try:
        e.admit(
            "speech.session.stop_playback", {"session_id": e.id, "synthesis_id": "s1"}
        )
        e.admit(
            "speech.session.finish_input",
            {"session_id": e.id, "stream_id": "mic", "end_sample": 0},
        )
        e.admit("speech.session.close", {"session_id": e.id, "mode": "drain"})
        await until(lambda: e.lifecycle == "failed")
        assert not any(event["type"] == "session_end" for event in parts[2])
        assert not e.current.playback_resolved
    finally:
        await e.shutdown()
