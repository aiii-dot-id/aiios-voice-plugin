import json

import pytest

from scripts.native_audit_io import AudioBytes, json_lines


def test_json_lines_preserves_rows_and_skips_only_log_diagnostics(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"sequence":1}\n{"sequence":2}\n')
    assert list(json_lines(path)) == [{"sequence": 1}, {"sequence": 2}]
    path.write_bytes(b'host diagnostic\n{"sequence":1}\n')
    assert list(json_lines(path, log=True)) == [{"sequence": 1}]
    with pytest.raises(json.JSONDecodeError):
        list(json_lines(path))


@pytest.mark.parametrize(
    "raw",
    [
        b'{"sequence":1}',
        b"x" * (1024 * 1024) + b"\n",
        ('{"text":"' + "\u00e9" * 600000 + '"}\n').encode(),
    ],
)
def test_json_lines_rejects_partial_or_byte_oversize(tmp_path, raw):
    path = tmp_path / "events.jsonl"
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="partial or oversized"):
        list(json_lines(path))


def test_audio_spans_are_exact_and_bounded(tmp_path, monkeypatch):
    path = tmp_path / "audio.f32le"
    path.write_bytes(b"0123456789")
    audio = AudioBytes(path)
    monkeypatch.setattr(
        type(path), "read_bytes", lambda *_: pytest.fail("whole recording read")
    )
    assert len(audio) == 10
    assert audio[2:5] == b"234"
    assert audio[10:10] == b""
    for span in [slice(-1, 2), slice(0, 11), slice(None, 2), slice(0, 4, 2), 3]:
        with pytest.raises(ValueError, match="span"):
            audio[span]
    path.write_bytes(b"01")
    with pytest.raises(ValueError, match="changed during audit"):
        audio[2:5]
