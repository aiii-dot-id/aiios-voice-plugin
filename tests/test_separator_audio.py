import numpy as np
import pytest

from scripts.separator_audio import normalized_sources, pcm16


def test_scale_invariant_output_is_not_saturated_before_uid():
    time = np.arange(16000, dtype=np.float32)/16000
    a = .15*np.sin(2*np.pi*193*time)
    b = .04*np.sin(2*np.pi*317*time)
    raw = [75*a, 75*b]
    before = [x.copy() for x in raw]
    outputs = normalized_sources(a+b, raw)
    for original, untouched, restored in zip(raw, before, outputs):
        np.testing.assert_array_equal(original, untouched)
        decoded = np.frombuffer(pcm16(restored), dtype='<i2').astype(np.float64)/32768
        assert np.max(np.abs(restored)) <= .95
        assert np.corrcoef(original, decoded)[0, 1] > .999999
        assert np.count_nonzero(np.abs(decoded) >= 32767/32768) == 0
    # The former helper quietly clipped these instead of naming the defect.
    with pytest.raises(ValueError):
        pcm16(raw[0])


@pytest.mark.parametrize('bad', [np.array([np.nan]), np.array([np.inf]),
    np.array([1.001]), np.array([-1.001]), np.array([]), np.zeros((1, 10)),
    np.array([-32768], dtype=np.int16), np.zeros(480001)])
def test_invalid_pcm_is_not_repaired_silently(bad):
    with pytest.raises(ValueError):
        pcm16(bad)
    with pytest.raises(ValueError):
        normalized_sources(bad, [bad, bad])


def test_second_channel_and_tail_are_validated():
    x = np.ones(80003, dtype=np.float32)*.1
    for bad in (x[:-3], np.full_like(x, np.nan), x.reshape(1, -1)):
        with pytest.raises(ValueError):
            normalized_sources(x, [x, bad])
    assert all(len(y) == len(x) for y in normalized_sources(x, [x, x]))
    with pytest.raises(ValueError):
        normalized_sources(x, [x])


def test_silence_does_not_divide_by_zero_or_manufacture_a_track():
    zero = np.zeros(32000, dtype=np.float32)
    speech = np.ones_like(zero)*.1
    assert all(np.isfinite(y).all() and not np.any(y)
               for y in normalized_sources(zero, [speech, zero]))
    outputs = normalized_sources(speech, [speech, zero])
    np.testing.assert_array_equal(outputs[1], zero)
    assert np.any(outputs[0])


@pytest.mark.parametrize('scale', [1e30, 1e-30, 1e300, 1e-300])
def test_pcm16_endpoint_roundtrip_and_dynamic_range(scale):
    assert np.frombuffer(pcm16(np.array([-1., 0., 1.])), dtype='<i2').tolist() == [-32768, 0, 32767]
    # Large scale-invariant estimates are attenuated, not overflowing their
    # energy calculation or generating NaNs during conversion.
    x = np.array([.1, -.2, .3], dtype=np.float64)
    sources = normalized_sources(x, [x*scale, x/scale])
    for y in sources:
        np.testing.assert_allclose(y, x, atol=1e-7)
