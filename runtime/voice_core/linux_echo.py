"""Byte-bound Linux AEC3 development dependency; no environment installation."""

import hashlib
import importlib
import sys
from pathlib import Path

from .echo import EchoFrontend

WHEEL = (
    "pywebrtc_audio-0.2.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl"
)
WHEEL_HASH = "e0cbe0afe02c7fa3b5bb7c47a64a145664a90141c504abac64bd55163b456c60"
FILES = {
    "__init__.py": "dccd4a89a3c0996a82a391a4efbaf41ecc787be6df98b2161a0ba0c0983823da",
    "_webrtc_audio.cpython-312-x86_64-linux-gnu.so": "63f03b0b146172d848f2e4f8e342737e1e095050af1118743104bd50ef6162e3",
}


def load_linux_echo(root):
    target = root / "artifacts/python"
    package = target / "pywebrtc_audio"
    for path, expected in {
        root / "artifacts/wheels" / WHEEL: WHEEL_HASH,
        **{package / name: value for name, value in FILES.items()},
    }.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"AEC binary identity differs: {path.name}")
    sys.path.insert(0, str(target))
    module = importlib.import_module("pywebrtc_audio")
    native = importlib.import_module("pywebrtc_audio._webrtc_audio")
    if any(
        Path(m.__file__).resolve().parent != package.resolve() for m in (module, native)
    ):
        raise ValueError("AEC import escaped verified package")
    processor = module.AudioProcessor(
        sample_rate=16000,
        echo_cancellation=True,
        noise_suppression=False,
        auto_gain_control=False,
    )
    return EchoFrontend(processor), {
        "package": "pywebrtc-audio",
        "version": "0.2.0",
        "wheel_sha256": WHEEL_HASH,
        "files": FILES,
        "backend": "native CPU AEC3",
        "noise_suppression": False,
        "auto_gain_control": False,
    }
