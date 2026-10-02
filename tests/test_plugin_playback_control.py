"""The proposed public receipt operation reuses the existing accounting owner."""

import asyncio
import time

import pytest

from runtime.plugin_engine.session import Refused
from tests.plugin_models import open_args
from tests.test_plugin_engine import parts as _engine_parts
from tests.test_plugin_engine import until
from tests.test_plugin_playback_receipts import completed

parts = _engine_parts
OP = "speech.session.playback_report"


def report(e, rendered=1337, terminal=True, **changes):
    return {
        "session_id": e.id,
        "synthesis_id": "s1",
        "output_stream": e.generations["s1"].stream,
        "rendered_samples": rendered,
        "terminal": terminal,
        **changes,
    }


@pytest.mark.asyncio
async def test_control_reports_exact_tail_during_drain_and_retries_idempotently(parts):
    e = await completed(parts)
    try:
        payload = report(e)
        e.admit(
            "speech.session.finish_input",
            {
                "session_id": e.id,
                "stream_id": "mic",
                "end_sample": 0,
            },
        )
        await until(e.input_done.is_set)
        e.admit("speech.session.close", {"session_id": e.id, "mode": "drain"})
        await asyncio.sleep(0)
        assert e.lifecycle == "draining"
        assert not any(x["type"] == "session_end" for x in parts[2])
        expected = {
            "accepted": True,
            **{k: v for k, v in payload.items() if k != "session_id"},
        }
        assert e.admit(OP, payload) == expected
        stable = e.status()
        assert e.admit(OP, payload) == expected
        assert e.status() == stable
        await until(lambda: e.lifecycle == "closed")
        events = [x for x in parts[2] if x["type"] == "playback_observation"]
        assert len(events) == 1 and events[0]["outcome"] == "drained"
        assert parts[2][-1]["status"] == "completed"
        assert parts[2][-1]["playback_verified"] is False
        with pytest.raises(Refused, match="STALE_SESSION"):
            e.admit(OP, payload)
    finally:
        await e.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes,code",
    [
        ({"session_id": "other"}, "STALE_SESSION"),
        ({"synthesis_id": "other"}, "PLAYBACK_REPORT"),
        ({"synthesis_id": []}, "PLAYBACK_REPORT"),
        ({"output_stream": True}, "PLAYBACK_REPORT"),
        ({"output_stream": 1.0}, "PLAYBACK_REPORT"),
        ({"rendered_samples": True}, "PLAYBACK_REPORT"),
        ({"rendered_samples": 1337.0}, "PLAYBACK_REPORT"),
        ({"rendered_samples": -1}, "PLAYBACK_REPORT"),
        ({"rendered_samples": 1338}, "PLAYBACK_REPORT"),
        ({"rendered_samples": 1336}, "PLAYBACK_REPORT"),
        ({"terminal": 1}, "PLAYBACK_REPORT"),
        ({"terminal": None}, "PLAYBACK_REPORT"),
        ({"outcome": "drained"}, "PLAYBACK_REPORT"),
    ],
)
async def test_control_refuses_bad_receipt_without_changing_state(parts, changes, code):
    e = await completed(parts)
    try:
        before = e.status()
        with pytest.raises(Refused, match=code):
            e.admit(OP, report(e, **changes))
        assert e.status() == before
    finally:
        await e.shutdown()


@pytest.mark.asyncio
async def test_receipt_control_cannot_wait_on_blocked_inference(parts):
    e, models, events, _ = parts
    try:
        e.admit("speech.session.open", open_args())
        await until(lambda: e.lifecycle == "open")
        models.stall = True
        e.admit(
            "speech.session.synthesize",
            {
                "session_id": e.id,
                "synthesis_id": "s1",
                "text": "In flight.",
            },
        )
        await until(models.blocked.is_set)
        payload = report(e, 0, terminal=False)
        before = time.perf_counter()
        assert e.admit(OP, payload)["accepted"] is True
        assert time.perf_counter() - before < 0.05
        with pytest.raises(Refused, match="output still live"):
            e.admit(OP, report(e, 0))
        e.admit(
            "speech.session.stop_playback", {"session_id": e.id, "synthesis_id": "s1"}
        )
        e.admit(
            "speech.session.cancel_synthesis",
            {"session_id": e.id, "synthesis_id": "s1"},
        )
        assert e.admit(OP, report(e, 0))["accepted"] is True
        assert events[-1]["outcome"] == "stopped"
        assert not e.current.terminal, "receipt is not inference retirement"
        models.release.set()
        await until(lambda: e.current.terminal)
        assert e.status()["playback"]["queued_samples"] == 0
    finally:
        models.release.set()
        await e.shutdown()
