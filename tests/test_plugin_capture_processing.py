"""Reported browser constraints never grant echo qualification or mute input."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from runtime.plugin_engine.audio import PCM, Frame
from runtime.plugin_engine.capture import capture_processing
from runtime.plugin_engine.session import Refused, ResidentEngine
from tests.plugin_models import Models, open_args


@pytest.mark.parametrize("value", [
    [], {"echo_cancellation": "yes"}, {"noise_suppression": 1},
    {"auto_gain_control": "false"}, {"sample_rate": True},
    {"sample_rate": 0}, {"sample_rate": float("nan")},
    {"tested": True}, {"tested": 0}, {"echo_verified": True},
])
def test_invalid_or_qualification_claim_is_refused(value):
    with pytest.raises(ValueError):
        capture_processing(value)


def test_unknown_is_not_raw_and_values_are_copied():
    assert capture_processing(None) is None
    value = {"echo_cancellation": None, "tested": False}
    copied = capture_processing(value)
    value["echo_cancellation"] = False
    assert copied == {"echo_cancellation": None, "tested": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("reported_aec", [True, False, None])
async def test_capture_report_is_observed_without_changing_speech_admission(reported_aec):
    models, events = Models(), []
    executor, control = ThreadPoolExecutor(1), ThreadPoolExecutor(1)

    async def write(frame, generation):
        return True

    engine = ResidentEngine(models, executor, control, events.append, write)
    try:
        args = open_args()
        args["audio"]["input"]["processing"] = {
            "echo_cancellation": reported_aec,
            "noise_suppression": True, "auto_gain_control": True,
            "sample_rate": 48000, "tested": False,
        }
        engine.admit("speech.session.open", args)
        async with asyncio.timeout(3):
            while engine.lifecycle != "open":
                await asyncio.sleep(0)
        observation = engine.status()["input"]["processing"]
        assert observation["reported"]["echo_cancellation"] is reported_aec
        assert not observation["echo_cancellation_verified"]
        assert not observation["engine_echo_cancellation"]
        observation["reported"]["tested"] = True
        assert engine.status()["input"]["processing"]["reported"]["tested"] is False
        engine.feed(Frame(PCM, 1, 0, 0, b"\0\x20" * 512))
        async with asyncio.timeout(3):
            while (not any(e["type"] == "speech_start" for e in events)
                   or len(models.samples) < 512):
                await asyncio.sleep(0)
        assert len(models.samples) == 512
    finally:
        await engine.shutdown()
        executor.shutdown(wait=True)
        control.shutdown(wait=True)


@pytest.mark.asyncio
async def test_bad_report_refuses_before_session_is_consumed():
    executor, control = ThreadPoolExecutor(1), ThreadPoolExecutor(1)
    engine = ResidentEngine(Models(), executor, control, lambda e: None, None)
    try:
        args = open_args()
        args["audio"]["input"]["processing"] = {"tested": True}
        with pytest.raises(Refused, match="CAPTURE_PROCESSING"):
            engine.admit("speech.session.open", args)
        assert engine.id is None
        assert not engine.session_ids
        assert not engine.tasks
        assert engine.lifecycle == "closed"
    finally:
        await engine.shutdown()
        executor.shutdown(wait=True)
        control.shutdown(wait=True)
