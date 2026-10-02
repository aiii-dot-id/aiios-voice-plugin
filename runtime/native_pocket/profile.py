"""Native TTS selection comes only from the carrier-verified runtime profile."""

from pathlib import Path

from runtime.physical_paths import existing_io_path

from runtime.model_assets import checked, read_json
from runtime.native_pocket.backend import ASSETS, digest


def selection(root, profile=None):
    if root is None:
        return None
    root = existing_io_path(root)
    if profile is None:
        profile, _ = read_json(checked(root, "voice-runtime.json"), max_bytes=32*1024**2)
    if "tts_backend" not in profile:
        if "native_pocket" in profile:
            raise ValueError("native Pocket configuration has no explicit selection")
        return None
    if (profile.get("platform"), profile.get("arch"), profile.get("backend"),
        profile.get("stt_backend"), profile["tts_backend"]) != (
        "windows", "amd64", "windows-pocket", "native-directml", "native-pocket-vulkan"
    ) or "pocket_execution" in profile:
        raise ValueError("unsupported or conflicting native Pocket profile")
    config = profile.get("native_pocket")
    if not isinstance(config, dict) or set(config) != {"library", "config", "threads", "seed", "max_steps"}:
        raise ValueError("native Pocket profile fields differ")
    if (type(config["threads"]) is not int or not 1 <= config["threads"] <= 4
            or type(config["seed"]) is not int or not 0 <= config["seed"] <= 2**32-1
            or type(config["max_steps"]) is not int or not 1 <= config["max_steps"] <= 750):
        raise ValueError("native Pocket execution bounds differ")
    paths = {}
    for key, prefix in (("library", "lib/native-pocket/"), ("config", "resources/native-pocket/")):
        name = config[key]
        if not isinstance(name, str) or not name.startswith(prefix) or name not in profile["files"]:
            raise ValueError("native Pocket resources must be runtime-owned")
        path = checked(root, name)
        row = profile["files"][name]
        if path.stat().st_size != row["bytes"] or digest(path) != row["sha256"]:
            raise ValueError("native Pocket resource binding differs")
        paths[key] = path
    if profile["files"][config["config"]]["sha256"] != ASSETS["config.yaml"]:
        raise ValueError("native Pocket checkpoint configuration differs")
    return {**config, **paths, "library_sha256": profile["files"][config["library"]]["sha256"]}
