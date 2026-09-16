"""Independent PCM decoding must reject damaged or reinterpreted evaluation input."""
import struct

import pytest

from scripts.audit_native_checkpoint_quality import pcm_bytes


def wav(data, *, code=1, rate=16000, channels=1, bits=16):
    width = channels * bits // 8
    fmt = struct.pack('<HHIIHH', code, channels, rate, rate * width, width, bits)
    body = b'WAVEfmt ' + struct.pack('<I', len(fmt)) + fmt
    body += b'data' + struct.pack('<I', len(data)) + data
    return b'RIFF' + struct.pack('<I', len(body)) + body


def test_pcm16_exact_scaling_and_boundaries():
    raw = wav(struct.pack('<hhhh', -32768, -1, 0, 32767))
    assert pcm_bytes(raw) == struct.pack('<ffff', -1, -1 / 32768, 0, 32767 / 32768)


def test_float32_is_not_normalized_or_rescaled():
    data = struct.pack('<fff', -.25, 0, .75)
    assert pcm_bytes(wav(data, code=3, bits=32)) == data


@pytest.mark.parametrize('kwargs', [dict(rate=8000), dict(channels=2), dict(code=2)])
def test_no_implicit_format_conversion(kwargs):
    with pytest.raises(AssertionError):
        pcm_bytes(wav(b'\x00\x00', **kwargs))


@pytest.mark.parametrize('value', [float('nan'), float('inf')])
def test_nonfinite_audio_is_refused(value):
    with pytest.raises(AssertionError):
        pcm_bytes(wav(struct.pack('<f', value), code=3, bits=32))


def test_truncation_is_not_a_smaller_valid_case():
    with pytest.raises(AssertionError):
        pcm_bytes(wav(b'\x00\x00')[:-1])


def test_missing_odd_chunk_padding_is_refused():
    raw = wav(b'\x00\x00') + b'JUNK\x01\x00\x00\x00x'
    raw = raw[:4] + struct.pack('<I', len(raw) - 8) + raw[8:]
    with pytest.raises(AssertionError, match='padding'):
        pcm_bytes(raw)
