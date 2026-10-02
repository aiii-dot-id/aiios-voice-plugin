from pathlib import Path

import pytest

from scripts.check_source_manifest import inventory, verify


def test_manifest_covers_every_tracked_file_and_exact_bytes(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"a")
    (tmp_path / "b.txt").write_bytes(b"b")
    names = ["MANIFEST.sha256", "a.txt", "b.txt"]
    (tmp_path / "MANIFEST.sha256").write_text(inventory(tmp_path, names))
    verify(tmp_path, names)

    (tmp_path / "c.txt").write_bytes(b"c")
    with pytest.raises(ValueError, match="does not exactly inventory"):
        verify(tmp_path, names + ["c.txt"])

    (tmp_path / "b.txt").write_bytes(b"changed")
    with pytest.raises(ValueError, match="does not exactly inventory"):
        verify(tmp_path, names)


def test_manifest_refuses_untracked_manifest_or_symlink(tmp_path: Path) -> None:
    (tmp_path / "source").write_bytes(b"source")
    (tmp_path / "MANIFEST.sha256").write_text(inventory(tmp_path, ["source"]))
    with pytest.raises(ValueError, match="must track"):
        verify(tmp_path, ["source"])
    (tmp_path / "alias").symlink_to("source")
    with pytest.raises(ValueError, match="invalid tracked source path"):
        inventory(tmp_path, ["alias", "source"])
