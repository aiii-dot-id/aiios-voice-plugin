"""The checksum file downloaded from a GitHub release verifies flat assets."""

import hashlib

import pytest

from scripts.stage_desktop_publication import release_checksums


def test_release_checksums_verify_flat_downloads(tmp_path):
    data = b"signed release asset"
    (tmp_path / "voice.aiiospkg").write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    (tmp_path / "SHA256SUMS").write_bytes(
        release_checksums([{"file": "assets/voice.aiiospkg", "sha256": digest}])
    )
    line = (tmp_path / "SHA256SUMS").read_text().strip()
    expected, name = line.split("  ", 1)
    assert name == "voice.aiiospkg"
    assert hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == expected


@pytest.mark.parametrize("path", ["voice.aiiospkg", "assets/nested/voice.aiiospkg", "../voice.aiiospkg"])
def test_release_checksums_refuse_nonflat_paths(path):
    with pytest.raises(ValueError):
        release_checksums([{"file": path, "sha256": "0" * 64}])
