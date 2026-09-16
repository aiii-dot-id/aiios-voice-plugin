"""WASAPI binding behind the shared native speech-session coordinator."""

import asyncio
from functools import partial

from runtime.cuda_voice.native import NativeBridge as SharedBridge

from .audio import WASAPIAudio, devices, validate_capture_mode
from .binding import digest


def binding(root, config):
    return {
        "backend": "wasapi",
        "source": config["source"],
        "sink": config["sink"],
        "capture_mode": validate_capture_mode(config.get("capture_mode", "shared")),
        "audio_source_sha256": digest(root / "runtime/windows_voice/audio.py"),
        "echo_frontend": "none_headphones_required_for_duplex",
        "credit_scope": "callback_submitted_plus_reported_DAC_deadline_not_acoustic",
    }


async def inventory(config=None):
    import sounddevice as sd

    rows = await asyncio.to_thread(devices, sd)
    if config:
        for key, direction in (("source", "input"), ("sink", "output")):
            matches = [
                r
                for r in rows
                if r["uid"] == config[key] and r[direction + "_channels"]
            ]
            if len(matches) != 1:
                raise RuntimeError("Bound Windows audio endpoint missing or ambiguous")
    return {"devices": rows, "defaults_changed": False}


class NativeBridge(SharedBridge):
    def __init__(self, root, expected, *args):
        super().__init__(
            root,
            expected,
            *args,
            binding_validator=binding,
            host_factory=partial(
                WASAPIAudio,
                source=expected["source"],
                sink=expected["sink"],
                capture_mode=expected["capture_mode"],
            ),
        )
