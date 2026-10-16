"""Late inference preserves the audio-clock turn and the live session."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import time

import numpy as np

from runtime.voice_core.semantic_endpoint import PauseGate


def test_delayed_semantic_verdict_or_acoustic_fallback():
    async def exercise(delay):
        events = []

        def predict(_pcm):
            time.sleep(delay)
            return .9

        with ThreadPoolExecutor(max_workers=1) as executor:
            gate = PauseGate(predict, executor, events.append)
            gate.append(np.ones(512, dtype=np.float32))
            assert await gate.poll(speech=False, silence=10240, position=10752) is None
            started = time.monotonic()
            result = await gate.poll(speech=False, silence=12288, position=12800)
            elapsed = time.monotonic() - started
            if delay < gate.decision_timeout_seconds:
                assert result == "semantic_no_hold"
                assert events[-1]["position"] == 10752
                assert events[-1]["resolution_position"] == 12800
            else:
                assert result is None and elapsed < 1.5
                assert await gate.poll(speech=False, silence=30720, position=31232) == "bounded_silence_fallback"
            await gate.close()
            if delay >= gate.decision_timeout_seconds:
                assert events[-1]["stale"] is True

    asyncio.run(exercise(.35))
    asyncio.run(exercise(1.2))
