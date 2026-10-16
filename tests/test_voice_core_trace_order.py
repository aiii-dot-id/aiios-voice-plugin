"""A synthesis is known to the trace before anything can name it."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from runtime.voice_core.live import Evidence, LiveSession
from tests.plugin_models import Models

IDENTITY = {"source_sha256": "0" * 64, "backend": "deterministic-test-not-real-model"}


def journal(ev):
    return [e["type"] for e in json.loads((ev.path / "trace.json").read_text())["events"]]


@pytest.fixture
def executor():
    executor = ThreadPoolExecutor(1)
    yield executor
    executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_a_barge_in_during_the_first_reply_write_keeps_the_trace_valid(
    tmp_path, executor
):
    sent, writing = [], asyncio.Event()

    async def send(message):
        sent.append(message)
        if message.get("type") == "event" and message["event"]["type"] == "application_event":
            writing.set()
            await asyncio.sleep(0)  # the notification write yields; a barge-in lands here

    ev = Evidence(tmp_path, IDENTITY, {}, "test")
    live = LiveSession(Models(), executor, send, ev, "A complete reply.")
    live.begin_synthesis("s1")
    await writing.wait()
    await live.interrupt("vad_speech")
    await live.synthesis_task
    ev.finish()  # the validator accepts the whole trace or raises
    kinds = journal(ev)
    assert kinds.index("synthesis_start") < kinds.index("interruption_requested")
    assert kinds.index("interruption_requested") < kinds.index("synthesis_cancelled")
    assert not [m for m in sent if m.get("type") == "audio"], "a barged-in reply was spoken"


@pytest.mark.asyncio
async def test_a_barge_in_before_the_reply_task_runs_still_cancels_it(tmp_path, executor):
    sent = []

    async def send(message):
        sent.append(message)

    ev = Evidence(tmp_path, IDENTITY, {}, "test")
    live = LiveSession(Models(), executor, send, ev, "A complete reply.")
    live.begin_synthesis("s1")
    await live.interrupt("vad_speech")  # no yield in between: the task has not run
    await live.synthesis_task
    ev.finish()
    done = [m for m in sent if m.get("type") == "synthesis_done"]
    assert done and done[0]["completed"] is False
    assert not [m for m in sent if m.get("type") == "audio"], "a barged-in reply was spoken"
    assert "interruption_requested" in journal(ev)


def resolved(ev, sid):
    """The terminal events journalled for one synthesis, after the validator ran."""
    events = json.loads((ev.path / "trace.json").read_text())["events"]
    own = [e for e in events if e.get("synthesis_id") == sid]
    assert own[0]["type"] == "synthesis_start", own
    return [e for e in own if e["type"] in ("synthesis_end", "synthesis_cancelled")]


@pytest.mark.asyncio
async def test_direct_awaitable_api_uses_registered_path(tmp_path, executor):
    async def send(message):
        if message.get("type") == "audio":
            live.acknowledged = message["end_sample"]
    ev = Evidence(tmp_path, IDENTITY, {}, "test")
    live = LiveSession(Models(), executor, send, ev, "A complete reply.")
    await live.synthesize("direct")
    ev.finish()
    assert [e["type"] for e in resolved(ev, "direct")] == ["synthesis_end"]


@pytest.mark.asyncio
async def test_a_reply_task_cancelled_before_it_runs_still_resolves_its_synthesis(
    tmp_path, executor
):
    sent = []

    async def send(message):
        sent.append(message)

    ev = Evidence(tmp_path, IDENTITY, {}, "test")
    live = LiveSession(Models(), executor, send, ev, "A complete reply.")
    task = live.begin_synthesis("s1")
    task.cancel()  # closing the session cancels the turn awaiting it, before this step
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert live.active_synthesis is None
    ev.finish()  # the validator refuses an unresolved synthesis_start
    (terminal,) = resolved(ev, "s1")
    assert terminal["type"] == "synthesis_cancelled"
    assert terminal["reason"] == "synthesis_task_cancelled"


@pytest.mark.asyncio
async def test_a_reply_whose_first_send_fails_still_resolves_its_synthesis(tmp_path, executor):
    async def send(message):
        raise ConnectionResetError("notification socket closed")

    ev = Evidence(tmp_path, IDENTITY, {}, "test")
    live = LiveSession(Models(), executor, send, ev, "A complete reply.")
    task = live.begin_synthesis("s1")
    (error,) = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(error, ConnectionResetError)
    assert live.active_synthesis is None and live.output_job is None
    ev.finish()
    (terminal,) = resolved(ev, "s1")
    assert terminal["type"] == "synthesis_cancelled"
    assert terminal["reason"] == "synthesis_task_failed"


@pytest.mark.asyncio
async def test_a_completed_or_interrupted_reply_is_resolved_exactly_once(tmp_path, executor):
    async def send(message):
        if message.get("type") == "audio":
            live.acknowledged = message["end_sample"]

    ev = Evidence(tmp_path, IDENTITY, {}, "test")
    live = LiveSession(Models(), executor, send, ev, "A complete reply.")
    await live.begin_synthesis("s1")
    await asyncio.sleep(0)  # the task's done callbacks have all run
    task = live.begin_synthesis("s2")
    await live.interrupt("vad_speech")
    await task
    await asyncio.sleep(0)
    ev.finish()
    assert [e["type"] for e in resolved(ev, "s1")] == ["synthesis_end"]
    assert [e["type"] for e in resolved(ev, "s2")] == ["synthesis_cancelled"]


@pytest.mark.asyncio
async def test_a_reply_ending_after_the_trace_ended_journals_nothing(tmp_path, executor):
    loop, faults = asyncio.get_running_loop(), []
    loop.set_exception_handler(lambda _, context: faults.append(context))

    async def send(message):
        pass

    ev = Evidence(tmp_path, IDENTITY, {}, "test")
    live = LiveSession(Models(), executor, send, ev, "A complete reply.")
    task = live.begin_synthesis("s1")
    ev.finish("failure", "session transport lost")
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    assert faults == []
    assert live.active_synthesis is None
    assert journal(ev)[-1] == "failure"
