"""The desktop audit binds declared settings, not a stale fixed count."""

import json

import pytest

from scripts.audit_native_desktop_family import declared_settings


def test_current_eight_settings_are_admitted():
    keys = ("stt_language", "turn_pause_ms", "capture_limit_minutes", "vad_threshold",
            "tts_voice", "tts_language", "tts_temperature", "tts_seed")
    settings = [{"key": key} for key in keys]
    assert declared_settings(json.dumps(settings).encode()) == settings


@pytest.mark.parametrize("bad", [[], [{"key": "a"}, {"key": "a"}], [{"key": ""}], [{"label": "no key"}]])
def test_missing_or_duplicate_setting_key_is_refused(bad):
    with pytest.raises(AssertionError):
        declared_settings(json.dumps(bad).encode())
