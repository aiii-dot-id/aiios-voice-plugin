"""Abort supersedes drain; admission is not native retirement or playback proof."""

import asyncio
import time

import pytest

from runtime.plugin_engine.audio import PCM
from runtime.plugin_engine.session import Refused
from tests.plugin_models import open_args
from tests.test_plugin_engine import parts as _engine_parts
from tests.test_plugin_engine import until
from tests.test_plugin_playback_receipts import completed

parts = _engine_parts


def close(e, mode, **changes):
    return e.admit(
        "speech.session.close", {"session_id": e.id, "mode": mode, **changes}
    )


def finish(e, end=0):
    return e.admit(
        "speech.session.finish_input",
        {"session_id": e.id, "stream_id": "mic", "end_sample": end},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("let_drain_start", [False, True])
async def test_abort_supersedes_receipt_wait_once_and_resident_can_reopen(
    parts, let_drain_start
):
    e = await completed(parts)
    _, _, events, _ = parts
    try:
        finish(e)
        await until(e.input_done.is_set)
        before = e.status()["playback"]
        assert before["delivered_samples"] == 1337
        assert before["rendered_samples"] == 0
        close(e, "drain")
        if let_drain_start:
            await asyncio.sleep(0)
        owners = set(e.tasks)
        started = time.perf_counter()
        assert close(e, "abort") == {"accepted": True, "mode": "abort"}
        assert time.perf_counter() - started < 0.05
        assert close(e, "abort") == {"accepted": True, "mode": "abort"}
        assert set(e.tasks) == owners, "Abort retries must not spawn cleanup owners"
        await until(lambda: e.lifecycle == "closed" and not e.tasks)
        ends = [x for x in events if x["type"] == "session_end"]
        assert len(ends) == 1 and ends[0]["status"] == "aborted"
        assert ends[0]["playback_verified"] is False
        assert e.status()["playback"] == before, "Abort cannot invent client evidence"
        assert not any(x["type"] == "playback_observation" for x in events)
        with pytest.raises(Refused, match="SESSION_STATE"):
            close(e, "abort")

        e.admit("speech.session.open", open_args("after-abort"))
        await until(lambda: e.lifecycle == "open")
        finish(e)
        await until(e.input_done.is_set)
        close(e, "drain")
        await until(lambda: e.lifecycle == "closed" and not e.tasks)
        assert events[-1]["session_id"] == "after-abort"
        assert events[-1]["status"] == "completed", "Abort must not poison reuse"
    finally:
        await e.shutdown()


@pytest.mark.asyncio
async def test_abort_during_drain_fences_now_but_waits_for_native_retirement(parts):
    e, models, events, frames = parts
    try:
        e.admit("speech.session.open", open_args())
        await until(lambda: e.lifecycle == "open")
        finish(e)
        await until(e.input_done.is_set)
        models.stall = True
        e.admit(
            "speech.session.synthesize",
            {"session_id": e.id, "synthesis_id": "blocked", "text": "Still running."},
        )
        await until(models.blocked.is_set)
        close(e, "drain")
        await asyncio.sleep(0)
        started = time.perf_counter()
        assert close(e, "abort")["accepted"] is True
        assert time.perf_counter() - started < 0.05
        assert e.current.fenced and e.current.cancelled
        await asyncio.sleep(0.01)
        assert e.lifecycle == "draining"
        assert not e.current.job.finished
        assert not any(x["type"] in ("session_end", "failure") for x in events)
        with pytest.raises(Refused, match="BUSY"):
            e.admit("speech.session.open", open_args("too-soon"))
        models.release.set()
        await until(lambda: e.lifecycle == "closed" and not e.tasks)
        assert e.current.job.finished
        assert events[-1]["status"] == "aborted"
        assert not any(f.kind == PCM for f in frames), "Fenced native tail escaped"
        assert not any(x["type"] == "playback_observation" for x in events)
    finally:
        models.release.set()
        await e.shutdown()


@pytest.mark.asyncio
async def test_abort_unresolved_input_does_not_invent_final_or_input_completion(parts):
    e, _, events, _ = parts
    try:
        e.admit("speech.session.open", open_args())
        await until(lambda: e.lifecycle == "open")
        finish(e, 400)
        close(e, "drain")
        await asyncio.sleep(0)
        close(e, "abort")
        await until(lambda: e.lifecycle == "closed" and not e.tasks)
        assert e.status()["input_completion"] is None
        assert not any(
            x["type"] in ("input_finished", "transcript_final", "failure")
            for x in events
        )
        assert events[-1]["status"] == "aborted"
    finally:
        await e.shutdown()


@pytest.mark.asyncio
async def test_abort_admitted_before_drain_terminal_wins_even_when_receipt_is_ready(
    parts,
):
    e = await completed(parts)
    try:
        finish(e)
        await until(e.input_done.is_set)
        close(e, "drain")
        e.admit(
            "speech.session.playback_report",
            {
                "session_id": e.id,
                "synthesis_id": "s1",
                "output_stream": e.current.stream,
                "rendered_samples": 1337,
                "terminal": True,
            },
        )
        close(e, "abort")  # no yield: terminal drain has not been published
        await until(lambda: e.lifecycle == "closed" and not e.tasks)
        assert parts[2][-1]["status"] == "aborted"
        assert len([x for x in parts[2] if x["type"] == "session_end"]) == 1
    finally:
        await e.shutdown()


@pytest.mark.asyncio
async def test_abort_cannot_relabel_inflight_failure_or_foreign_session(parts):
    e, models, events, _ = parts
    try:
        e.admit("speech.session.open", open_args())
        await until(lambda: e.lifecycle == "open")
        models.stall = True
        e.admit(
            "speech.session.synthesize",
            {"session_id": e.id, "synthesis_id": "blocked", "text": "In flight."},
        )
        await until(models.blocked.is_set)
        finish(e)
        close(e, "drain")
        with pytest.raises(Refused, match="STALE_SESSION"):
            close(e, "abort", session_id="foreign")
        e.fail(RuntimeError("endpoint lost"))
        with pytest.raises(Refused, match="SESSION_STATE"):
            close(e, "abort")
        models.release.set()
        await until(lambda: e.lifecycle == "failed" and not e.tasks)
        assert events[-1]["reason"] == "endpoint lost"
        assert not any(x["type"] == "session_end" for x in events)
    finally:
        models.release.set()
        await e.shutdown()
