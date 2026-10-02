"""Development audio-preservation candidate, not a promoted runtime path.

The host would need to retain both raw and cleaned pre-roll until VAD admission.
This never repairs text: it restores actual mic PCM around the observed stop.
"""

import numpy as np

LOOKBACK = 6144  # 384 ms of retained microphone history
AFTER_STOP = 3200  # 200 ms for the opening word / render-tail transition
FADE = 160  # 10 ms; do not introduce hard PCM discontinuities


def preserve(raw, clean, stop_sample):
    if raw.ndim != 1 or raw.shape != clean.shape or not len(raw):
        raise ValueError("matching mono recordings required")
    if not np.isfinite(raw).all() or not np.isfinite(clean).all():
        raise ValueError("nonfinite prefix PCM")
    if (
        isinstance(stop_sample, bool)
        or not isinstance(stop_sample, int)
        or not 0 < stop_sample <= len(raw)
    ):
        raise ValueError("observed VAD sample required")
    start, end = max(0, stop_sample - LOOKBACK), min(len(raw), stop_sample + AFTER_STOP)
    gain = np.ones(end - start, np.float32)
    width = min(FADE, len(gain) // 2)
    if width:
        gain[:width] = np.linspace(0, 1, width, dtype=np.float32)
        gain[-width:] = np.linspace(1, 0, width, dtype=np.float32)
    out = clean.copy()
    out[start:end] = clean[start:end] * (1 - gain) + raw[start:end] * gain
    return out, {
        "start": start,
        "end": end,
        "lookback": LOOKBACK,
        "after_stop": AFTER_STOP,
        "fade": FADE,
    }
