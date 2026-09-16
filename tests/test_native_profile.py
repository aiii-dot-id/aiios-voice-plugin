import json

import pytest

from plugin import runtime_bootstrap as boot
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


def test_bootstrap_native_lane_uses_profile_only(monkeypatch, tmp_path):
    value, catalog = profile(tmp_path)
    data = tmp_path / "host-models"
    data.mkdir()
    called = []
    monkeypatch.setattr(boot, "prepare", lambda **kw: (tmp_path, value, data))
    monkeypatch.setattr(boot.sys, "argv", ["bootstrap", "--stt"])
    monkeypatch.setattr(
        boot.runpy,
        "run_module",
        lambda module, **kw: called.append((module, boot.sys.argv[:], kw)),
    )
    boot.main()
    assert called == [
        (
            "runtime.stt.native_resident",
            ["packaged-stt", "--model-assets", str(catalog), "--root", str(data)],
            {"run_name": "__main__"},
        )
    ]
    monkeypatch.setattr(
        boot.sys, "argv", ["bootstrap", "--stt", "--native-root", "other-model"]
    )
    with pytest.raises(RuntimeError, match="no external selection"):
        boot.main()
    assert len(called) == 1


def test_native_inspection_never_imports_torch_or_transformers(
    monkeypatch, tmp_path, capsys
):
    value, _ = profile(tmp_path)
    imported = []
    monkeypatch.setattr(boot, "prepare", lambda **kw: (tmp_path, value, tmp_path))
    monkeypatch.setattr(boot.sys, "argv", ["bootstrap", "--inspect-stt"])
    monkeypatch.setattr(
        boot.importlib, "import_module", lambda name: imported.append(name)
    )
    monkeypatch.setattr(boot.importlib.metadata, "version", lambda name: name)
    monkeypatch.setattr(boot, "module_files", lambda *a: {})
    monkeypatch.setattr(boot, "native_images", lambda *a: [])
    boot.main()
    assert imported == ["numpy", "onnxruntime"]
    assert set(json.loads(capsys.readouterr().out)["versions"]) == {
        "numpy",
        "onnxruntime-directml",
    }
