"""Calibrated diagnostic: subtract predicted render, never scale microphone.

Known far-only calibration is required; this is not an autonomous AEC policy.
The FIR is frozen after fit, so arbitrary added near speech passes unchanged
up to floating-point roundoff. No model/text/VAD determines the output.
"""

import numpy as np
from scipy.signal import lfilter

TAPS = 2048
FFT = 4096
HOP = 1024
RIDGE = 0.01


def mono(values):
    values = np.asarray(values, dtype=np.float64)
    if values.ndim != 1 or not len(values) or not np.isfinite(values).all():
        raise ValueError("nonempty finite mono audio required")
    return values


def fit(render, microphone):
    render, microphone = mono(render), mono(microphone)
    if render.shape != microphone.shape or len(render) < FFT * 3:
        raise ValueError("matching bounded calibration required")
    if len(render) > 16000 * 4:
        raise ValueError("calibration exceeds four seconds")
    window = np.hanning(FFT)
    power = np.zeros(FFT // 2 + 1)
    cross = np.zeros(FFT // 2 + 1, np.complex128)
    count = 0
    for start in range(0, len(render) - FFT + 1, HOP):
        x = np.fft.rfft(render[start : start + FFT] * window)
        y = np.fft.rfft(microphone[start : start + FFT] * window)
        power += np.abs(x) ** 2
        cross += y * np.conj(x)
        count += 1
    power /= count
    cross /= count
    ridge = RIDGE * float(np.mean(power))
    if ridge < 1e-12:
        raise ValueError("calibration has no render excitation")
    impulse = np.fft.irfft(cross / (power + ridge), n=FFT)
    taps = impulse[:TAPS].copy()
    return taps, {
        "taps": TAPS,
        "fft": FFT,
        "hop": HOP,
        "ridge_fraction": RIDGE,
        "windows": count,
        "ridge": ridge,
        "retained_impulse_energy_fraction": float(
            np.dot(taps, taps) / max(1e-30, np.dot(impulse, impulse))
        ),
        "maximum_tap_sample": int(np.argmax(np.abs(taps))),
        "coefficient_l2": float(np.linalg.norm(taps)),
    }


class ReferencePath:
    def __init__(self, taps):
        self.taps = mono(taps).copy()
        if len(self.taps) != TAPS:
            raise ValueError("exact fixed FIR size required")
        self.state = np.zeros(TAPS - 1)
        self.samples = 0

    def push(self, microphone, render):
        microphone, render = mono(microphone), mono(render)
        if microphone.shape != render.shape:
            raise ValueError("aligned render and mic required")
        predicted, self.state = lfilter(self.taps, [1.0], render, zi=self.state)
        self.samples += len(microphone)
        return (microphone - predicted).astype(np.float32)
