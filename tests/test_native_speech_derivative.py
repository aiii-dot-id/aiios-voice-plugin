import json
from types import SimpleNamespace

import pytest

from scripts import execute_windows_native_speech as owner
from scripts.package_native_runtime import runtime_inventory, sha256, verify


def test_startup_derivative_preserves_dependencies_and_baseline(tmp_path, monkeypatch):
    code, source, output = [tmp_path / name for name in ("source-code", "baseline", "candidate")]
    code.mkdir()
    source.mkdir()
    for name in ("runtime/model_assets.py", "runtime/stt/profile.py"):
        old, new = source / "engine" / name, code / name
        old.parent.mkdir(parents=True, exist_ok=True)
        new.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(b"old engine")
        new.write_bytes(b"fixed engine")
    (source / "dependency.dll").write_bytes(b"preserve exactly")
    body = {"schema": "aiii.voice.native-runtime", "qualified": False,
            "files": runtime_inventory(source), "stt_backend": "native-directml"}
    (source / "voice-runtime.json").write_text(json.dumps(body))
    digest = sha256(source / "voice-runtime.json")
    monkeypatch.setattr(owner, "ROOT", code)
    monkeypatch.setattr(owner, "RUNTIME", digest)
    monkeypatch.setattr(owner.shutil, "disk_usage", lambda p: SimpleNamespace(free=8 * 1024**3))
    after = owner.refresh_engine(source, output)
    assert verify(source, digest) == body
    result = verify(output, after)
    assert result["stt_backend"] == "native-directml"
    assert (source / "dependency.dll").stat().st_ino == (output / "dependency.dll").stat().st_ino
    assert (output / "engine/runtime/model_assets.py").read_bytes() == b"fixed engine"
    assert result["startup_inventory_fix"]["baseline_manifest_sha256"] == digest
    with pytest.raises(ValueError, match="fresh"):
        owner.refresh_engine(source, output)
    monkeypatch.setattr(owner.shutil, "disk_usage", lambda p: SimpleNamespace(free=4 * 1024**3))
    with pytest.raises(ValueError, match="reserve"):
        owner.refresh_engine(source, tmp_path / "no-space")
    assert not (tmp_path / "no-space").exists()
