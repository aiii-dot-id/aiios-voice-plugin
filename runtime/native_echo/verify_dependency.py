"""Verify the exact dependency working bytes, not only Git's cached status."""

import hashlib
from pathlib import Path
import subprocess
import sys

REVISION = "2cec2f52e26646f93bd2d5498bbabf59cba18da9"


def verify(root):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args])

    if git("rev-parse", "HEAD").decode().strip() != REVISION:
        raise ValueError("wrong AEC3 revision")
    if git("ls-files", "--others", "-z"):
        raise ValueError("untracked dependency source could enter the build")
    for entry in git("ls-tree", "-rz", "HEAD").split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, kind, digest = metadata.split()
        path = root / name.decode()
        if kind != b"blob" or mode not in (b"100644", b"100755") or path.is_symlink():
            raise ValueError("unsupported dependency entry")
        data = path.read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if actual != digest.decode():
            raise ValueError("dependency bytes differ: " + name.decode())


if __name__ == "__main__":
    verify(Path(sys.argv[1]).resolve(strict=True))
