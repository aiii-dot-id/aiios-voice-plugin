"""Input and playback-reference chunks are gapless and non-overlapping PER STREAM,
as the protocol says: two byte-bound input kinds cannot both occupy one stream's
samples (review, 2026-09-16)."""

import numpy as np
import pytest

from runtime.voice_core.live import Evidence

IDENTITY = {"source_sha256": "0" * 64, "backend": "deterministic-test-not-real-model"}


def test_input_kinds_share_one_gapless_position_per_stream(tmp_path):
    pcm = np.zeros(512, np.float32)
    ev = Evidence(tmp_path, IDENTITY, {}, "test")
    ev.audio("input_audio", pcm, 0, stream_id="microphone")
    ev.audio("playback_reference", pcm, 0, stream_id="microphone")  # overlaps
    with pytest.raises(ValueError, match="must start at sample 512"):
        ev.finish()
    ev = Evidence(tmp_path, IDENTITY, {}, "test")
    ev.audio("input_audio", pcm, 0)  # microphone
    ev.audio("playback_reference", pcm, 0)  # playback-reference: its own stream
    ev.finish()
