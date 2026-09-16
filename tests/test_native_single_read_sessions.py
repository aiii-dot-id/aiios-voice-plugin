import json
from types import SimpleNamespace

import pytest

from scripts import native_single_read_sessions as run
from scripts.package_native_runtime import runtime_inventory, sha256, verify


def test_derivative_changes_only_the_verifier_and_preserves_baseline(
    tmp_path, monkeypatch
):
    before, after, patch = (
        tmp_path / "baseline",
        tmp_path / "candidate",
        tmp_path / "new.py",
    )
    target = before / "engine" / run.PATCH
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old verifier")
    (before / "unchanged.dll").write_bytes(b"same native dependency")
    patch.write_bytes(b"new verifier")
    profile = {
        "schema": "aiii.voice.native-runtime",
        "qualified": False,
        "files": runtime_inventory(before),
    }
    (before / "voice-runtime.json").write_text(json.dumps(profile))
    digest = sha256(before / "voice-runtime.json")
    monkeypatch.setattr(run, "NEW_VERIFIER", sha256(patch))
    monkeypatch.setattr(
        run.shutil, "disk_usage", lambda _: SimpleNamespace(free=8 * 1024**3)
    )
    candidate = run.derive(before, after, patch, digest)
    result = verify(after, candidate)
    assert verify(before, digest) == profile
    assert result["files"]["unchanged.dll"] == profile["files"]["unchanged.dll"]
    assert (before / "unchanged.dll").stat().st_ino == (
        after / "unchanged.dll"
    ).stat().st_ino
    assert target.read_bytes() == b"old verifier"
    assert (after / "engine" / run.PATCH).read_bytes() == b"new verifier"
    with pytest.raises(ValueError, match="fresh"):
        run.derive(before, after, patch, digest)
    monkeypatch.setattr(
        run.shutil, "disk_usage", lambda _: SimpleNamespace(free=4 * 1024**3)
    )
    with pytest.raises(ValueError, match="reserve"):
        run.derive(before, tmp_path / "no-space", patch, digest)
    assert not (tmp_path / "no-space").exists()
