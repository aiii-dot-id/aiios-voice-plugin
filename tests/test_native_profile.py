"""The Python double's choice of its recognizer's model catalog from a runtime profile.

Two cases that were here went with what they tested, the packaged interpreter's
bootstrap, which is removed. What stays is the double's own reading of a profile.
"""
import json

import pytest

from runtime.stt.profile import native_catalog
from runtime.model_assets import read_json


def profile(tmp_path):
    name = "resources/model-assets/catalog.json"
    catalog = tmp_path / name
    catalog.parent.mkdir(parents=True)
    catalog.write_text("{}")
    value = {
        "backend": "windows-pocket",
        "stt_backend": "native-directml",
        "model_assets": name,
        "files": {name: {}},
    }
    (tmp_path / "voice-runtime.json").write_text(json.dumps(value))
    return value, catalog


def test_absent_selection_preserves_existing_profile(tmp_path):
    assert native_catalog(tmp_path, {"backend": "cuda"}) is None
    assert native_catalog(tmp_path, {"backend": "windows-pocket"}) is None


def test_real_size_runtime_inventory_is_not_a_small_model_catalog(tmp_path):
    value, catalog = profile(tmp_path)
    value["files"].update({f"deps/file-{i:05d}.py": {"sha256": "a" * 64, "bytes": 1}
                           for i in range(14628)})
    path = tmp_path / "voice-runtime.json"
    path.write_text(json.dumps(value))
    assert path.stat().st_size > 1024**2
    assert native_catalog(tmp_path) == catalog.resolve()
    # The fix must not relax untrusted model-catalog metadata globally.
    with pytest.raises(ValueError, match="metadata exceeds"):
        read_json(path)
    with path.open("wb") as stream:
        stream.truncate(32 * 1024**2 + 1)
    with pytest.raises(ValueError, match="metadata exceeds"):
        native_catalog(tmp_path)


@pytest.mark.parametrize(
    "damage", ["backend", "unknown", "undeclared", "outside", "escape", "link"]
)
def test_native_selection_refuses_other_backend_or_catalog(tmp_path, damage):
    value, catalog = profile(tmp_path)
    if damage == "backend":
        value["backend"] = "mlx"
    elif damage == "unknown":
        value["stt_backend"] = "automatic"
    elif damage == "undeclared":
        value["files"] = {}
    elif damage in {"outside", "escape"}:
        value["model_assets"] = (
            "model-data/catalog.json"
            if damage == "outside"
            else "resources/model-assets/../../catalog.json"
        )
        value["files"][value["model_assets"]] = {}
    elif damage == "link":
        catalog.unlink()
        catalog.symlink_to(tmp_path / "voice-runtime.json")
    with pytest.raises(ValueError):
        native_catalog(tmp_path, value)
