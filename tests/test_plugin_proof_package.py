"""The proof package must carry every input its executable build sealed."""

import hashlib
import json
import zipfile

import pytest

from scripts import package_plugin_windows_proof as packaging


def test_source_archive_accepts_exact_external_carrier_not_arbitrary_files(tmp_path):
    from scripts.prove_plugin_sdk_engine import capture_source_archive
    root = tmp_path / "source"
    root.mkdir()
    source = root / "engine.py"
    source.write_bytes(b"code")
    carrier = tmp_path / "installed.exe"
    carrier.write_bytes(b"entrypoint")
    archive = tmp_path / "evidence.zip"
    hashes = capture_source_archive(archive, [source, carrier], root=root, carrier=carrier)
    with zipfile.ZipFile(archive) as z:
        assert {n: hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist()} == hashes
        assert z.read("__runtime_artifact__/installed.exe") == b"entrypoint"
    other = tmp_path / "unrelated"
    other.write_bytes(b"not in scope")
    with pytest.raises(ValueError, match="only the bound carrier"):
        capture_source_archive(tmp_path / "bad.zip", [other], root=root, carrier=carrier)
    collision = root / "__runtime_artifact__/installed.exe"
    collision.parent.mkdir()
    collision.write_bytes(b"collision")
    with pytest.raises(ValueError, match="duplicate"):
        capture_source_archive(tmp_path / "collision.zip", [carrier, collision], root=root, carrier=carrier)


@pytest.mark.parametrize("missing", [False, True])
def test_package_uses_build_inventory_not_only_extension_globs(tmp_path, monkeypatch, missing):
    monkeypatch.setattr(packaging, "ROOT", tmp_path)
    monkeypatch.setattr(packaging, "SDK_SOURCE", tmp_path / ".build/sdk")
    monkeypatch.setattr(packaging, "BUILD_DIR", tmp_path / ".build/carrier")
    packaging.BUILD_DIR.mkdir(parents=True)
    extra = "plugin/native/build-input-without-go-suffix"
    calls = []
    def verified(*, output):
        calls.append(output)
        return {"inputs": {extra: "unused-test-binding"}}
    monkeypatch.setattr(packaging, "verify_build", verified)
    names = ["plugin/sdk-source.json", "plugin/runtime_bootstrap.py", packaging.PIN["archive"],
             "tests/plugin_worker_fixture.py", "tests/plugin_delayed_ack_fixture.py",
             "tests/plugin_models.py", "tests/test_plugin_engine.py",
             "deliverables/speech-output/validation-20260907-r2/recovery.wav"]
    if not missing:
        names.append(extra)
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    output = tmp_path / "proof.zip"
    if missing:
        with pytest.raises(ValueError, match="not a regular source file"):
            packaging.package(output)
    else:
        fresh = tmp_path / ".build/fresh-carrier"
        fresh.mkdir()
        (fresh / "verified.exe").write_bytes(b"fresh")
        (packaging.BUILD_DIR / "stale.exe").write_bytes(b"stale")
        packaging.package(output, build_dir=fresh)
        assert calls == [fresh, fresh]
        with zipfile.ZipFile(output) as zf:
            assert zf.read(".build/fresh-carrier/verified.exe") == b"fresh"
            assert ".build/carrier/stale.exe" not in zf.namelist()
            assert zf.read(extra) == extra.encode()
            assert extra in json.loads(zf.read("proof-source-manifest.json"))
            assert zf.read("plugin/runtime_bootstrap.py") == b"plugin/runtime_bootstrap.py"


def test_package_refuses_external_build_before_archive_creation(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(packaging, "ROOT", root)
    with pytest.raises(ValueError):
        packaging.package(root / "proof.zip", build_dir=tmp_path)
    assert not (root / "proof.zip").exists()
