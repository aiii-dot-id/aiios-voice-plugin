import base64
import hashlib
import unittest

import numpy as np
from scipy.signal import lfilter

from runtime.voice_core.native_stream import Decimator48k, NativePCM


def ready(offset=0):
    return {
        "input_sample_rate": 48000,
        "render_sample_rate": 48000,
        "host_tick_frequency": 24000000,
        "voice_processing_enabled": True,
        "voice_processing_bypassed": False,
    }


def row(stream, values, start=0, offset=0):
    raw = np.asarray(values, dtype="<f4").tobytes()
    return {
        "stream": stream,
        "frames": len(values),
        "start_sample": start,
        "sample_rate": 48000,
        "hardware_sample_time": start,
        "hardware_host_ticks": 24000000 + (start + offset) * 500,
        "content_sha256": hashlib.sha256(raw).hexdigest(),
        "pcm_f32le": base64.b64encode(raw).decode(),
    }


class NativeStreamTests(unittest.TestCase):
    def test_chunking_keeps_every_sample_phase_and_final_phoneme(self):
        x = np.random.default_rng(9).normal(0, 0.01, 9017).astype(np.float32)
        x[-1] = 0.5
        reference = Decimator48k()
        expected = lfilter(reference.taps, [1], np.pad(x, (0, 126)))[::3].astype(
            np.float32
        )
        actual = Decimator48k()
        blocks = []
        for start in range(0, len(x), 101):
            blocks.append(actual.push(x[start : start + 101]))
        blocks.append(actual.finish())
        np.testing.assert_allclose(
            np.concatenate(blocks), expected, atol=1e-8, rtol=1e-6
        )
        self.assertGreater(float(np.max(np.abs(blocks[-1]))), 0.05)
        with self.assertRaisesRegex(ValueError, "ended"):
            actual.push(x)

    def test_fast_vad_does_not_wait_for_reference_and_alignment_is_explicit(self):
        pcm = NativePCM(ready())
        mic = np.ones(4800, np.float32) * 0.1
        self.assertEqual(pcm.push(row("microphone", mic)), [])
        fast = pcm.fast_blocks()
        self.assertEqual(len(fast), 3)
        pairs = pcm.push(row("render", mic, offset=1660))
        self.assertEqual(len(pairs), 3)
        for a, b in zip(fast, pairs):
            np.testing.assert_array_equal(a, b[0])
        self.assertTrue(pcm.can_finish())
        tail, meta = pcm.finish()
        fast_tail = pcm.fast_blocks()
        self.assertEqual(len(tail), len(fast_tail))
        for a, b in zip(fast_tail, tail):
            np.testing.assert_array_equal(a, b[0])
        self.assertEqual(meta["reference_offset_48k_samples"], 1660)
        self.assertEqual(meta["native_microphone_samples"], 4800)
        self.assertEqual(
            len(pairs + tail) * 512, 1600 + 42 + meta["final_16k_padding_samples"]
        )

    def test_negative_offset_waits_for_reference_coverage(self):
        pcm = NativePCM(ready())
        x = np.zeros(4800, np.float32)
        pcm.push(row("render", x, offset=-1600))
        pcm.push(row("microphone", x))
        self.assertFalse(pcm.can_finish())
        pcm.push(row("render", x, start=4800, offset=-1600))
        self.assertTrue(pcm.can_finish())
        pcm.finish()

    def test_corruption_and_hardware_discontinuity_are_not_drops(self):
        pcm = NativePCM(ready())
        x = np.zeros(4800, np.float32)
        value = row("microphone", x)
        value["content_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "identity"):
            pcm.push(value)
        pcm.push(row("microphone", x))
        value = row("microphone", x, start=4800)
        value["hardware_sample_time"] += 1
        with self.assertRaisesRegex(ValueError, "clock jumped"):
            pcm.push(value)

    def test_bad_clock_and_processing_are_refused(self):
        for field, value in (
            ("host_tick_frequency", float("nan")),
            ("voice_processing_enabled", False),
        ):
            r = ready()
            r[field] = value
            with self.assertRaises(ValueError):
                NativePCM(r)
