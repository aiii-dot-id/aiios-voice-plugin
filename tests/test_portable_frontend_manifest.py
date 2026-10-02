"""Only transforms with frozen numerical evidence enter the portable ABI."""

import ctypes
from pathlib import Path

import numpy as np
import pytest

from runtime.audio_frontend.native import NativeFrontend, build_shared_library
from runtime.audio_frontend.reference import ReferenceFrontend, parse_manifest


ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "runtime/audio_frontend/manifests"
NAMES = ("causal-logmel-16k-80-v1", "causal-logmel-48k-to-16k-80-v1")


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    return build_shared_library(tmp_path_factory.mktemp("frontend") / "frontend.so")


def native_accepts(library, raw):
    lib = ctypes.CDLL(str(library))
    lib.vf_frontend_create.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)]
    lib.vf_frontend_create.restype = ctypes.c_int
    lib.vf_frontend_destroy.argtypes = [ctypes.c_void_p]
    storage = ctypes.create_string_buffer(raw)
    handle = ctypes.c_void_p()
    status = lib.vf_frontend_create(storage, len(raw), ctypes.byref(handle))
    if handle.value:
        lib.vf_frontend_destroy(handle)
    return status == 0


@pytest.mark.parametrize("name", NAMES)
def test_frozen_transforms_still_run(library, name):
    raw = (MANIFESTS / (name + ".frontend")).read_bytes()
    config = parse_manifest(raw)
    assert native_accepts(library, raw)
    count = config.input_sample_rate_hz // 5
    pcm = (np.sin(np.arange(count) * .047) * 9000).astype("<i2").tobytes()
    reference = ReferenceFrontend(raw)
    with NativeFrontend(library, raw) as native:
        expected = np.concatenate((reference.push(pcm).features, reference.flush().features))
        actual = np.concatenate((native.push(pcm), native.flush()))
    np.testing.assert_allclose(actual, expected, atol=2e-4, rtol=2e-5)


@pytest.mark.parametrize("name,old,new", [
    (NAMES[1], b"input_sample_rate_hz=48000", b"input_sample_rate_hz=44100"),
    (NAMES[0], b"frame_length_samples=400", b"frame_length_samples=7"),
    (NAMES[0], b"hop_length_samples=160", b"hop_length_samples=3"),
    (NAMES[0], b"fft_size=512", b"fft_size=8"),
    (NAMES[0], b"mel_bins=80", b"mel_bins=79"),
    (NAMES[1], b"resample_filter_taps=63", b"resample_filter_taps=61"),
    (NAMES[1], b"resample_cutoff_ratio_ppm=940000", b"resample_cutoff_ratio_ppm=930000"),
])
def test_unqualified_transform_is_refused_by_c_and_reference(library, name, old, new):
    raw = (MANIFESTS / (name + ".frontend")).read_bytes()
    assert old in raw
    changed = raw.replace(old, new)
    with pytest.raises(ValueError):
        parse_manifest(changed)
    assert not native_accepts(library, changed)
