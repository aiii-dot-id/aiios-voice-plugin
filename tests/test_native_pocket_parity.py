import numpy as np
import pytest

from scripts.prove_native_pocket_parity import compare_audio
from scripts.windows_native_pocket_gate import compare, executable_row


@pytest.fixture(params=[compare_audio, compare], ids=["numpy", "windows-stdlib"])
def gate(request):
    def run(a, b, *args):
        return request.param(list(a), list(b), *args)
    return run


@pytest.fixture
def audio():
    return np.sin(np.arange(4800, dtype=np.float64) * 0.019) * 0.15


def test_exact_audio_passes(audio, gate):
    assert gate(audio, audio.copy())["passed"]


def test_pcm16_rounding_passes(audio, gate):
    rounded = np.round(audio * 32767) / 32767
    assert gate(audio, rounded)["passed"]


def test_identical_prefix_missing_tail_fails(audio, gate):
    assert not gate(audio, audio[:-1920])["passed"]


def test_invented_tail_fails(audio, gate):
    assert not gate(audio, np.pad(audio, (0, 1920)))["passed"]


def test_correlated_wrong_amplitude_fails(audio, gate):
    assert not gate(audio, audio * 0.5)["passed"]


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_nonfinite_refused(audio, bad, gate):
    changed = audio.copy()
    changed[0] = bad
    assert not gate(audio, changed)["passed"]
    assert not gate(changed, audio)["passed"]


def test_silent_and_empty_refused(gate):
    assert not gate([], [])["passed"]
    assert not gate(np.zeros(4800), np.zeros(4800))["passed"]


@pytest.mark.parametrize("rate,channels", [(16000, 1), (24000, 2)])
def test_format_mismatch_refused(audio, rate, channels, gate):
    assert not gate(audio, audio, rate, channels)["passed"]


@pytest.mark.parametrize("path", [r"build\bin\Release\audiocpp_cli.exe", "build/bin/Release/audiocpp_cli.exe"])
def test_native_windows_build_paths(path):
    row = {"name": path, "sha256": "bound"}
    assert executable_row([row, {"name": "build/bin/Release/pocket_tts_warm_bench.exe"}]) is row


@pytest.mark.parametrize("rows", [[], [{"name": "other.exe"}],
    [{"name": "a/audiocpp_cli.exe"}, {"name": "b/audiocpp_cli.exe"}],
    [{"name": "../audiocpp_cli.exe"}], [{"name": r"C:\audiocpp_cli.exe"}]])
def test_ambiguous_or_unsafe_build_path_refused(rows):
    with pytest.raises(ValueError):
        executable_row(rows)
