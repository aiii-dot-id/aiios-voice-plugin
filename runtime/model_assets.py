"""Read-only installed model groups, bound by the signed runtime's catalog.

No downloader, cache discovery, filesystem materializer or import-path changes.
The host owns acquisition and pins the verified data tree for the activation.
Development loaders retain their explicit existing paths when this is absent.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath

from runtime.physical_paths import existing_io_path, path_identity

SCHEMA = "aiii.voice.installed-model-assets"
MAX_FILES = 128
MAX_FILE_BYTES = 16 * 1024**3
# Raw ONNX external initializer bytes are data, never an import/execution path.
DATA_SUFFIXES = {".json", ".safetensors", ".onnx", ".bin", ".model", ".txt", ".md"}
RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def relative(name):
    """Portable model-data names, not platform-dependent path normalization."""
    if not isinstance(name, str):
        raise ValueError("model path must be text")
    p = PurePosixPath(name)
    if not name or not p.parts or len(name) > 240 or p.is_absolute() or str(p) != name:
        raise ValueError("unsafe model path")
    for part in p.parts:
        if (
            part in (".", "..")
            or part[-1:] in (".", " ")
            or part.split(".")[0].casefold() in RESERVED
            or any(ord(c) < 32 or ord(c) > 126 or c in '<>:"\\|?*' for c in part)
        ):
            raise ValueError("unsafe model path")
    return p


def checked(root, name, *, directory=False):
    path = root
    parts = relative(name).parts
    for index, part in enumerate(parts):
        path = path / part
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400:
            raise ValueError("model path cannot be a link or reparse point")
        want_dir = index < len(parts) - 1 or directory
        if not (stat.S_ISDIR(st.st_mode) if want_dir else stat.S_ISREG(st.st_mode)):
            raise ValueError("model path has the wrong file type")
    return path


def object_pairs(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate model catalog key")
        result[name] = value
    return result


def read_json(path, *, max_bytes=1024 * 1024):
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("metadata byte limit must be a positive integer")
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400:
        raise ValueError("model metadata must be a regular file")
    if st.st_size > max_bytes:
        raise ValueError(f"metadata exceeds {max_bytes} bytes")
    # A metadata path replaced after lstat must not turn this read into a FIFO
    # wait. The runtime owner still pins parent directories for the activation.
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("model metadata must be a regular file")
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError(f"metadata exceeds {max_bytes} bytes")
    return json.loads(raw, object_pairs_hook=object_pairs), raw


def file_hashes(path, size):
    """One bounded read; never follow a replacement FIFO or read an append forever."""
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    with os.fdopen(os.open(path, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size != size:
            raise ValueError("model file type/size changed")
        sha = hashlib.sha256()
        blob = hashlib.sha1(f"blob {size}\0".encode(), usedforsecurity=False)
        remaining = size
        while remaining:
            chunk = stream.read(min(remaining, 8 * 1024 * 1024))
            if not chunk:
                raise ValueError("model file truncated during verification")
            remaining -= len(chunk)
            sha.update(chunk)
            blob.update(chunk)
        if stream.read(1):
            raise ValueError("model file grew during verification")
        after = os.fstat(stream.fileno())
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ValueError("model file changed during verification")
    return sha.hexdigest(), blob.hexdigest()


class ModelAssets:
    def __init__(self, catalog, data_root, *, backend):
        catalog, data_root = Path(catalog), Path(data_root)
        if catalog.is_symlink() or data_root.is_symlink():
            raise ValueError("model catalog/root cannot be a link")
        if getattr(data_root.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("model root cannot be a reparse point")
        self.catalog = existing_io_path(catalog)
        self.root = existing_io_path(data_root)
        if not self.root.is_dir() or path_identity(self.catalog, strict=True).is_relative_to(
            path_identity(self.root, strict=True)
        ):
            raise ValueError("model catalog must be runtime-owned, outside model data")
        body, _ = read_json(self.catalog)
        if (
            set(body) != {"schema", "backend", "groups"}
            or body["schema"] != SCHEMA
            or body["backend"] != backend
        ):
            raise ValueError("model catalog backend/schema differs")
        self.backend = backend
        self.groups = body["groups"]
        if not isinstance(self.groups, dict) or not 1 <= len(self.groups) <= 16:
            raise ValueError("invalid model groups")
        self.manifests = {}
        destinations = set()
        directories = set()
        for role, group in self.groups.items():
            if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", role):
                raise ValueError("invalid model role")
            if set(group) != {"directory", "manifest", "manifest_sha256", "files"}:
                raise ValueError("unknown model group fields")
            directory = relative(group["directory"])
            normalized = str(directory).casefold()
            if any(
                normalized == old
                or normalized.startswith(old + "/")
                or old.startswith(normalized + "/")
                for old in directories
            ):
                raise ValueError("model group directories overlap")
            directories.add(normalized)
            path = checked(self.catalog.parent, group["manifest"])
            manifest, raw = read_json(path)
            if hashlib.sha256(raw).hexdigest() != group["manifest_sha256"]:
                raise ValueError("model acquisition manifest binding differs")
            source_rows = manifest["files"]
            expected = {row["path"]: row for row in source_rows}
            files = group["files"]
            if (
                len(expected) != len(source_rows)
                or set(files) != set(expected)
                or not files
            ):
                raise ValueError("model catalog and acquisition file sets differ")
            for name, row in files.items():
                p = relative(name)
                if p.name != ".gitattributes" and p.suffix.lower() not in DATA_SUFFIXES:
                    raise ValueError("unsupported installed model data format")
                if (
                    set(row) != {"sha256", "bytes"}
                    or type(row["bytes"]) is not int
                    or not 0 < row["bytes"] <= MAX_FILE_BYTES
                ):
                    raise ValueError("invalid model file bound")
                if not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]):
                    raise ValueError("invalid model SHA-256")
                if row["bytes"] != expected[name]["size"] or (
                    expected[name].get("lfs_sha256")
                    and row["sha256"] != expected[name]["lfs_sha256"]
                ):
                    raise ValueError("model asset differs from acquisition binding")
                full = str(directory / p).casefold()
                if any(
                    full == old
                    or full.startswith(old + "/")
                    or old.startswith(full + "/")
                    for old in destinations
                ):
                    raise ValueError("model destination collision")
                destinations.add(full)
            self.manifests[role] = manifest
        if len(destinations) > MAX_FILES:
            raise ValueError("installed model file count exceeds bound")

    def snapshot(self, role):
        """Verify the complete role once at each loader admission; never cache it."""
        group = self.groups[role]
        root = checked(self.root, group["directory"], directory=True)
        expected = group["files"]
        allowed_dirs = {
            str(p) for n in expected for p in PurePosixPath(n).parents if str(p) != "."
        }
        found = set()
        pending = [(root, "")]
        while pending:
            parent, prefix = pending.pop()
            for path in parent.iterdir():
                name = prefix + path.name
                st = path.lstat()
                if (
                    stat.S_ISLNK(st.st_mode)
                    or getattr(st, "st_file_attributes", 0) & 0x400
                ):
                    raise ValueError("model data link/reparse point refused")
                if stat.S_ISDIR(st.st_mode) and name in allowed_dirs:
                    pending.append((path, name + "/"))
                elif stat.S_ISREG(st.st_mode) and name in expected:
                    if st.st_size != expected[name]["bytes"]:
                        raise ValueError("model file size differs: " + name)
                    found.add(name)
                else:
                    raise ValueError("unexpected installed model path: " + name)
        if found != set(expected):
            raise ValueError("installed model files missing")
        source = {row["path"]: row for row in self.manifests[role]["files"]}
        for name, row in expected.items():
            path = checked(root, name)
            sha, blob = file_hashes(path, row["bytes"])
            if sha != row["sha256"]:
                raise ValueError("installed model SHA-256 differs: " + name)
            if not source[name].get("lfs_sha256") and blob != source[name].get(
                "git_blob"
            ):
                raise ValueError("installed model git blob differs: " + name)
        return root

    def manifest_sha(self, role):
        return self.groups[role]["manifest_sha256"]
