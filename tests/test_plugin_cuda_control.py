"""CUDA-recognition isolation and independent backend cancellation contracts."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from runtime.plugin_engine.session import ResidentEngine
from tests.plugin_models import Models, open_args
from tests.test_plugin_engine import until


@pytest.mark.asyncio
async def test_recognition_and_cancel_do_not_queue_behind_tts():
    models = Models()
    events, cancels = [], []
    models.cancel_synthesis = lambda: cancels.append(True)

    async def write(*args):
        return True

    with (
        ThreadPoolExecutor(1) as compute,
        ThreadPoolExecutor(1) as vad,
        ThreadPoolExecutor(1) as recognition,
    ):
        models.recognition_executor = recognition
        e = ResidentEngine(models, compute, vad, events.append, write)
        try:
            e.admit("speech.session.open", open_args())
            await until(lambda: e.lifecycle == "open")
            models.stall = True
            e.admit(
                "speech.session.synthesize",
                {"session_id": e.id, "synthesis_id": "blocked", "text": "Hold."},
            )
            await until(models.blocked.is_set)
            # The real CUDA adapter uses this lane for its resident recognizer.
            # Routing it onto compute instead makes this bounded await fail.
            heard = await asyncio.wait_for(
                e.recognition.gpu(lambda: "opening words"), 0.5
            )
            assert heard == "opening words" and not models.release.is_set()
            e.admit(
                "speech.session.stop_playback",
                {"session_id": e.id, "synthesis_id": "blocked"},
            )
            assert not cancels, (
                "stop-playback must not silently become compute cancellation"
            )
            e.admit(
                "speech.session.cancel_synthesis",
                {"session_id": e.id, "synthesis_id": "blocked"},
            )
            assert cancels == [True]
            assert not e.current.terminal, "admission is not worker retirement"
        finally:
            models.release.set()
            await e.shutdown()
