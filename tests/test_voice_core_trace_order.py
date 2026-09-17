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
