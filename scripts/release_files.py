"""File helpers the release staging tools share: digests, exclusive writes, checked copies."""
import hashlib
import json
from pathlib import Path
import shutil


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def emit(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')


def put(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)


def copy_asset(source, target, size, expected):
    source = Path(source)
    if source.is_symlink() or not source.is_file() or source.stat().st_size != size:
        raise ValueError('asset source kind/size differs: ' + source.name)
    if sha(source) != expected:
        raise ValueError('asset source hash differs: ' + source.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Independent inodes: a future edit of release staging must not edit the
    # retained model/companion via a hard link. Only the 377 MB release set copies.
    with source.open('rb') as src, target.open('xb') as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
    if target.stat().st_size != size or sha(target) != expected or sha(source) != expected:
        raise ValueError('asset changed during staging: ' + source.name)


def census(root):
    result = {}
    for p in root.rglob('*'):
        if p.is_symlink() or not (p.is_file() or p.is_dir()):
            raise ValueError('nonregular release input')
        if p.is_file():
            result[p.relative_to(root).as_posix()] = {'sha256': sha(p), 'size': p.stat().st_size}
    return result
