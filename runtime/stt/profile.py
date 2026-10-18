from pathlib import Path

from runtime.model_assets import checked, read_json
from runtime.physical_paths import existing_io_path, path_identity


def native_catalog(root, profile=None):
    root = existing_io_path(root)
    if profile is None:
        profile, _ = read_json(checked(root, "voice-runtime.json"), max_bytes=32 * 1024**2)
    if "stt_backend" not in profile:
        return None
    if (
        profile["stt_backend"] != "native-directml"
        or profile["backend"] != "windows-pocket"
    ):
        raise ValueError("unsupported explicit packaged STT backend")
    name = profile.get("model_assets")
    if (
        not isinstance(name, str)
        or not name.startswith("resources/model-assets/")
        or name not in profile["files"]
    ):
        raise ValueError("native STT requires a runtime-owned installed catalog")
    catalog = checked(root, name)
    path_identity(catalog, strict=True).relative_to(
        path_identity(root / "resources/model-assets", strict=True)
    )
    return catalog
