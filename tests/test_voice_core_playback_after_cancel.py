"""Playback of audio buffered before a cancellation is a modelled state: the
validator admits playback_start for a cancelled synthesis as it already admits
interruption_requested for one."""

import pytest

from runtime.voice_core.live import Evidence

IDENTITY = {"source_sha256": "0" * 64, "backend": "deterministic-test-not-real-model"}


def test_playback_after_a_cancelled_synthesis_is_a_modelled_state(tmp_path):
    ev = Evidence(tmp_path, IDENTITY, {}, "test")
    ev.emit("synthesis_start", synthesis_id="s1")
    ev.emit("synthesis_cancelled", synthesis_id="s1", reason="vad_speech")
    ev.emit("playback_start", synthesis_id="s1")
    ev.emit("playback_stop", synthesis_id="s1")
    ev.finish()
    ev = Evidence(tmp_path, IDENTITY, {}, "test")
    ev.emit("playback_start", synthesis_id="never-started")
    with pytest.raises(ValueError, match="no known synthesis"):
        ev.finish()
