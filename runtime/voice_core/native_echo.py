"""Load the isolated, byte-bound AEC3 wheel; never mutate the MLX environment."""

import hashlib
import importlib
import sys
from pathlib import Path

from .echo import EchoFrontend

WHEEL = "pywebrtc_audio-0.2.0-cp311-cp311-macosx_11_0_arm64.whl"
WHEEL_SHA256 = "b6b80b00590e1a2dc588906e381a2aebc4d5c5707687541014797b8f02b1223f"
FILES = {
    "__init__.py": "dccd4a89a3c0996a82a391a4efbaf41ecc787be6df98b2161a0ba0c0983823da",
    "_webrtc_audio.cpython-311-darwin.so": "20d4e63b6233ea42fb797b895c8fb6b9ef94b1b9c679a1dd1a6bd8068eab3208",
}


def load_native_echo(root: Path):
    target = root / "artifacts/python/pywebrtc-audio-0.2.0"
    package = target / "pywebrtc_audio"
    expected = {root / "artifacts/wheels" / WHEEL: WHEEL_SHA256}
    expected.update({package / name: digest for name, digest in FILES.items()})
    for path, digest in expected.items():
        if (
            not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            raise RuntimeError(f"exact AEC dependency missing or changed: {path.name}")
    sys.path.insert(0, str(target))
    module = importlib.import_module("pywebrtc_audio")
    native = importlib.import_module("pywebrtc_audio._webrtc_audio")
    if (
        Path(module.__file__).resolve().parent != package.resolve()
        or Path(native.__file__).resolve().parent != package.resolve()
    ):
        raise RuntimeError("AEC import resolved outside the verified package")
    identity = {
        "name": "pywebrtc-audio",
        "version": "0.2.0",
        "backend": "WebRTC AEC3 / native CPU DSP",
        "wheel": WHEEL,
        "wheel_sha256": WHEEL_SHA256,
        "files": FILES,
        "frame_samples": 160,
        "sample_rate_hz": 16000,
        "delay_policy": "causal render alignment, two observations, bounded past-only warm start",
        "max_history_seconds": 3,
        "adaptation_replay_seconds": 2,
        "render_tail_seconds": 1,
        "noise_suppression": False,
        "auto_gain_control": False,
    }

    def factory():
        return EchoFrontend(
            module.AudioProcessor(
                sample_rate=16000,
                echo_cancellation=True,
                noise_suppression=False,
                auto_gain_control=False,
            )
        )

    return factory, identity
