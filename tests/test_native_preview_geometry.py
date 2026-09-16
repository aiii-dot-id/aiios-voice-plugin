import numpy as np
import pytest

from scripts.probe_native_preview_geometry import CHUNKS, feature_window, windows


@pytest.mark.parametrize("context", [0, 3, 6])
def test_context_geometry_never_skips_or_repeats_centers(context):
    centers = []
    for start, count, drop in windows(context):
        centers.extend(start + 8 * i for i in range(drop, (count + 7) // 8))
    assert centers == list(range(0, 8 * (context + 1) * CHUNKS[context], 8))
    assert len(centers) > 56  # Exercise eviction of the fixed attention cache.


def test_sub_stride_initial_history_is_padding_not_future_audio():
    samples = np.arange(100 * 128, dtype="f4").reshape(100, 128)
    start, count, drop = list(windows(0))[1]
    assert (start, count, drop) == (-8, 17, 2)
    actual = feature_window(samples, start, count)
    np.testing.assert_array_equal(actual[:8], 0)
    np.testing.assert_array_equal(actual[8:], samples[:9])


@pytest.mark.parametrize("value", [-1, 1, 13, True, 3.0])
def test_unsupported_geometry_refused(value):
    with pytest.raises(ValueError, match="unsupported"):
        list(windows(value))
