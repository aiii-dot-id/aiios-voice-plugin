"""Optional exact-math frontend, selected only by the bound runtime manifest."""

from pathlib import Path

from runtime.model_assets import checked, read_json
from runtime.physical_paths import existing_io_path
from runtime.windows_voice.pocket_execution import selection as pocket_selection


def selection(root, profile=None):
    if root is None:
        return None
    root = existing_io_path(root)
    if profile is None:
        profile, _ = read_json(checked(root, "voice-runtime.json"), max_bytes=32*1024**2)
    if "endpoint_frontend" not in profile:
        return None
    config = profile["endpoint_frontend"]
    if not isinstance(config, dict) or set(config) != {"kind", "coefficients"}:
        raise ValueError("endpoint frontend fields differ")
    native = profile.get("tts_backend") == "native-pocket-vulkan" and "pocket_execution" not in profile
    int8 = "tts_backend" not in profile and pocket_selection(root, profile) is not None
    if config["kind"] != "whisper-torch-exact" or (
        profile.get("platform"), profile.get("arch"), profile.get("backend"),
        profile.get("stt_backend")
    ) != ("windows", "amd64", "windows-pocket", "native-directml") or not (native or int8):
        raise ValueError("endpoint frontend composition is not bound for this profile")
    name = config["coefficients"]
    files = profile.get("files", {})
    if not isinstance(name, str) or not name.startswith("resources/endpoint/") or name not in files:
        raise ValueError("endpoint coefficients must be runtime-owned")
    row = files[name]
    if (row.get("bytes") != 64320 or not isinstance(row.get("sha256"), str)
            or len(row["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in row["sha256"])):
        raise ValueError("invalid endpoint coefficient binding")
    # The frontend checks the bytes against this one inventory authority before
    # using them. checked() also rejects redirection outside the runtime.
    return {"coefficients": checked(root, name), "sha256": row["sha256"]}
