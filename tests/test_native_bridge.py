import asyncio
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import numpy as np

from runtime.voice_core.live import Evidence
from runtime.voice_core.native_bridge import NativeBridge
from tests.test_native_stream import ready
from tests.test_native_stream import row as audio_row
from tests.test_voice_live import FakeModels


def native_audio(stream, values, start=0):
    return dict(
        type="audio",
        sequence=2 + start // len(values) * 2 + (stream == "render"),
        **audio_row(stream, values, start=start),
    )


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.evidence = Evidence(
            self.temp.name,
            {"source_sha256": "b" * 64, "source_files": {}},
            {},
            "test_fixture",
        )
        self.messages = []
        self.commands = []

        async def send(row):
            self.messages.append(row)

        with patch("scripts.probe_macos_audio_host.identity", return_value={}):
            self.bridge = NativeBridge(
                Path(self.temp.name),
                {},
                FakeModels(),
                self.executor,
                send,
                self.evidence,
                "reply",
                "in",
                "out",
            )

        async def command(row):
            self.commands.append(row)

        self.bridge.command = command

    async def asyncTearDown(self):
        await self.bridge.close()
        self.evidence.finish("cancellation", "unit test teardown")
        self.executor.shutdown(wait=True)
        self.temp.cleanup()

    async def test_speech_cancels_while_gpu_is_blocked(self):
        entered = threading.Event()
        release = threading.Event()

        def occupied():
            entered.set()
            release.wait(5)

        waiting = self.executor.submit(occupied)
        while not entered.is_set():
            await asyncio.sleep(0.001)

        class PCM:
            def fast_blocks(self):
                return [np.ones(512, np.float32) * 0.1]

        class VAD:
            def feed(self, block):
                return 0.9

        self.bridge.pcm = PCM()
        self.bridge.vad = VAD()
        session = self.bridge.session
        self.evidence.emit("synthesis_start", synthesis_id="s1")
        session.active_synthesis = "s1"
        session.playing.add("s1")
        started = time.monotonic()
        try:
            await asyncio.wait_for(self.bridge.control_blocks(), 0.25)
            self.assertLess(time.monotonic() - started, 0.25)
            self.assertFalse(waiting.done(), "test did not hold the GPU busy")
            self.assertEqual(self.commands, [{"type": "cancel", "synthesis_id": "s1"}])
            self.assertTrue(session.cancel_generation)
            self.assertEqual(list(self.bridge.probabilities), [0.9])
        finally:
            release.set()
            waiting.result(5)

    async def test_cancelled_completion_and_late_audio_cannot_revive_reply(self):
        import base64

        self.bridge.sent_audio.add("s1")
        await self.bridge.model_message(
            {"type": "interrupt", "synthesis_id": "s1", "reason": "speech"}
        )
        await self.bridge.model_message(
            {"type": "synthesis_done", "synthesis_id": "s1", "completed": True}
        )
        await self.bridge.model_message(
            {
                "type": "audio",
                "synthesis_id": "s1",
                "sample_rate": 24000,
                "end_sample": 10,
                "pcm_f32le": base64.b64encode(
                    np.zeros(10, np.float32).tobytes()
                ).decode(),
            }
        )
        self.assertEqual(self.commands, [{"type": "cancel", "synthesis_id": "s1"}])

    async def test_audio_split_preserves_final_short_chunk_and_full_count(self):
        import base64

        x = np.arange(8001, dtype=np.float32) / 16000
        await self.bridge.model_message(
            {
                "type": "audio",
                "synthesis_id": "s2",
                "sample_rate": 24000,
                "end_sample": len(x),
                "pcm_f32le": base64.b64encode(x.tobytes()).decode(),
            }
        )
        await self.bridge.model_message(
            {"type": "synthesis_done", "synthesis_id": "s2", "completed": True}
        )
        self.assertEqual([c["end_sample"] for c in self.commands[:2]], [7680, 8001])
        self.assertEqual(
            b"".join(base64.b64decode(c["pcm_f32le"]) for c in self.commands[:2]),
            x.tobytes(),
        )
        self.assertEqual(
            self.commands[-1], {"type": "synthesis_done", "synthesis_id": "s2"}
        )
        self.assertFalse(any(m["type"] == "audio" for m in self.messages))

    async def prepare_host(self):
        class VAD:
            def feed(self, block):
                return 0.0

        self.bridge.vad = VAD()
        await self.bridge.native_event(
            dict(type="ready", input={"uid": "in"}, output={"uid": "out"}, **ready())
        )

    async def test_ready_requires_validated_paired_audio_not_engine_start(self):
        await self.prepare_host()
        self.assertFalse(any(m["type"] == "ready" for m in self.messages))
        x = np.zeros(4800, np.float32)
        await self.bridge.native_event(native_audio("microphone", x))
        self.assertFalse(any(m["type"] == "ready" for m in self.messages))
        await self.bridge.native_event(native_audio("render", x))
        messages = [m for m in self.messages if m["type"] == "ready"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(
            messages[0]["capture"]["native_samples"],
            {"microphone": 4800, "render": 4800},
        )
        self.assertEqual(messages[0]["capture"]["admitted_16k_samples"], 1536)
        for stream in ("microphone", "render"):
            await self.bridge.native_event(native_audio(stream, x, start=4800))
        self.assertEqual(sum(m["type"] == "ready" for m in self.messages), 1)

    async def test_startup_failure_never_advertises_live_microphone(self):
        await self.prepare_host()
        with self.assertRaisesRegex(RuntimeError, "stopped on configuration change"):
            await self.bridge.native_event(
                {
                    "type": "complete",
                    "status": "failed",
                    "reason": "error",
                    "error": "audio engine stopped on configuration change",
                }
            )
        self.assertFalse(any(m["type"] == "ready" for m in self.messages))

    async def test_corrupt_reference_never_advertises_ready(self):
        await self.prepare_host()
        x = np.zeros(4800, np.float32)
        await self.bridge.native_event(native_audio("microphone", x))
        bad = native_audio("render", x)
        bad["content_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "identity"):
            await self.bridge.native_event(bad)
        self.assertFalse(any(m["type"] == "ready" for m in self.messages))

    async def test_control_events_cannot_hide_missing_audio_progress(self):
        await self.prepare_host()
        with self.assertRaisesRegex(TimeoutError, "startup did not supply"):
            self.bridge.check_progress(now=21, started=0, last_event=21)
        self.bridge.capture_ready = True
        self.bridge.last_admitted_at = 20
        with self.assertRaisesRegex(TimeoutError, "audio admission stalled"):
            self.bridge.check_progress(now=24, started=0, last_event=24)
        # Once input is explicitly finished, playback may continue without input.
        self.bridge.model_input_done = True
        self.bridge.check_progress(now=24, started=0, last_event=24)

    async def test_finish_before_validated_audio_is_refused(self):
        await self.prepare_host()
        with self.assertRaisesRegex(ValueError, "not become ready"):
            await self.bridge.end()

    async def test_duration_limit_closes_input_once_without_stopping_playback(self):
        await self.prepare_host()
        x = np.zeros(4800, np.float32)
        for stream in ("microphone", "render"):
            await self.bridge.native_event(native_audio(stream, x))
        await self.bridge.maybe_end_for_duration(
            self.bridge.ready_at + self.bridge.input_limit_seconds - 0.01
        )
        self.assertFalse(self.bridge.ending)
        await self.bridge.maybe_end_for_duration(
            self.bridge.ready_at + self.bridge.input_limit_seconds
        )
        await self.bridge.maybe_end_for_duration(
            self.bridge.ready_at + self.bridge.input_limit_seconds + 1
        )
        self.assertTrue(self.bridge.model_input_done)
        self.assertEqual(self.bridge.input_cutoff, 4800)
        self.assertEqual(sum(m["type"] == "input_closed" for m in self.messages), 1)
        self.assertFalse(
            any(c["type"] in {"stop", "cancel", "finish"} for c in self.commands)
        )
        self.assertEqual(
            self.bridge.host_limit_seconds, self.bridge.input_limit_seconds + 60
        )
