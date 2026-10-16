"""Verify that MANIFEST.sha256 binds every tracked source file exactly once."""

import hashlib
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def inventory(root: Path, names: list[str]) -> str:
    records = []
    for name in sorted(names):
        if name == "MANIFEST.sha256":
            continue
        path = root / name
        if path.is_symlink() or not path.is_file() or "\n" in name or "\\" in name:
            raise ValueError(f"invalid tracked source path: {name}")
        records.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {name}\n")
    return "".join(records)


def verify(root: Path, names: list[str]) -> None:
    if names.count("MANIFEST.sha256") != 1 or len(names) != len(set(names)):
        raise ValueError("source inventory must track each file and its manifest once")
    manifest = (root / "MANIFEST.sha256").read_text()
    expected = inventory(root, names)
    if manifest != expected:
        raise ValueError("MANIFEST.sha256 does not exactly inventory tracked source; regenerate after staging changes")


def main() -> None:
    names = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-files", "-z", "--cached"]
    ).decode().rstrip("\0").split("\0")
    verify(ROOT, names)
    print(f"source inventory: {len(names) - 1} tracked files bound")


if __name__ == "__main__":
    main()
