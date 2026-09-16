import hashlib
import json
import zipfile

import pytest

from scripts.audit_native_pocket_followup import verify_archive


def package(tmp_path, *, name="proof.json", corrupt=False, omitted=False, extra=False, duplicate_manifest=False):
    raw = b'{"passed": false}\n'
    manifest = [{"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}]
    if duplicate_manifest:
        manifest += manifest
    path = tmp_path / "evidence.zip"
    with zipfile.ZipFile(path, "w") as out:
        if not omitted:
            out.writestr(name, raw + b"!" if corrupt else raw)
        if extra:
            out.writestr("undeclared.json", b"{}")
        out.writestr("archive-manifest.json", json.dumps(manifest))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_expected_failure_evidence_is_preserved(tmp_path):
    path, sha = package(tmp_path)
    out = tmp_path / "readback"
    verify_archive(path, sha, out)
    assert json.loads((out / "proof.json").read_text()) == {"passed": False}


@pytest.mark.parametrize("options", [
    {"corrupt": True}, {"omitted": True}, {"extra": True}, {"duplicate_manifest": True},
    {"name": "../escape"}, {"name": "/absolute"}, {"name": "C:/escape"}, {"name": "bad\\path"},
])
def test_changed_or_unsafe_evidence_refused(tmp_path, options):
    path, sha = package(tmp_path, **options)
    with pytest.raises(ValueError):
        verify_archive(path, sha, tmp_path / "readback")
    assert not (tmp_path / "readback").exists()


def test_native_archive_binding_required(tmp_path):
    path, _ = package(tmp_path)
    with pytest.raises(ValueError, match="native receipt"):
        verify_archive(path, "0" * 64, tmp_path / "readback")


def test_existing_evidence_not_overwritten(tmp_path):
    path, sha = package(tmp_path)
    out = tmp_path / "readback"
    out.mkdir()
    with pytest.raises(FileExistsError):
        verify_archive(path, sha, out)
