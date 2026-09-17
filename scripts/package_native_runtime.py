"""Freeze the actual Mac inference interpreter, dependency closure and engine.

Produces a relocatable native-code payload for the SDK packaging seam. No
dependency install, model copy, credential copy, download or deployment occurs.
This is not a signed package and does not grant downloaded models code authority.
"""

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import sysconfig
from importlib import metadata
from pathlib import Path, PurePosixPath

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
ROOT_REQUIREMENTS = (
    "mlx-audio",
    "torch",
    "onnxruntime",
    "aiohttp",
    "sentencepiece",
    "tiktoken",
)


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def dependency_closure(roots, distribution=metadata.distribution):
    """Honor active environment markers AND requested transitive extras."""
    environment = default_environment()
    resolved, pending, active_extras = {}, list(roots), {}
    while pending:
        requirement = Requirement(pending.pop())
        name = canonicalize_name(requirement.name)
        dist = distribution(name)
        if requirement.specifier and dist.version not in requirement.specifier:
            raise ValueError(
                f"installed dependency conflicts with {requirement}: {dist.version}"
            )
        extras = set(requirement.extras) | {""}
        more = extras - active_extras.get(name, set())
        if not more:
            continue
        active_extras.setdefault(name, set()).update(more)
        resolved[name] = dist
        for value in dist.requires or []:
            child = Requirement(value)
            if child.marker is None or any(
                child.marker.evaluate({**environment, "extra": extra}) for extra in more
            ):
                pending.append(str(child))
    return resolved


def safe_relative(name):
    value = PurePosixPath(name)
    if (
        not name
        or name == "."
        or ":" in name
        or value.is_absolute()
        or ".." in value.parts
        or "\\" in name
        or str(value) != name
    ):
        raise ValueError(f"unsafe runtime path: {name}")
    return value


def runtime_inventory(root, *, target_platform=None):
    files = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"runtime symlink refused: {path}")
        if path.is_dir():
            continue
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError("non-regular runtime payload")
        name = path.relative_to(root).as_posix()
        carrier = "aii-voice-t3.exe" if (target_platform or sys.platform) in ("win32", "windows") else "aii-voice-t3"
        if name in ("voice-runtime.json", carrier):
            continue
        files[name] = {
            "sha256": sha256(path),
            "bytes": path.stat().st_size,
            "executable": bool(path.stat().st_mode & 0o111),
        }
    return files


def verify(root, expected):
    manifest = root / "voice-runtime.json"
    if root.is_symlink() or manifest.is_symlink() or sha256(manifest) != expected:
        raise ValueError("runtime manifest digest or root binding differs")
    body = json.loads(manifest.read_text())
    # This verifies byte custody, not release qualification. The historical
    # label may be either boolean; neither value bypasses inventory checks.
    if body["schema"] != "aiii.voice.native-runtime" or type(body.get("qualified")) is not bool:
        raise ValueError("unsupported runtime profile")
    for name in body["files"]:
        safe_relative(name)
    if runtime_inventory(root, target_platform=body["platform"]) != body["files"]:
        raise ValueError("runtime payload differs from bound inventory")
    return body


def build(output):
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise ValueError("this first runtime profile is Mac arm64 only")
    base = Path(sys.base_prefix).resolve()
    site = Path(sysconfig.get_path("platlib")).resolve()
    if not (base / "lib/libpython3.11.dylib").is_file():
        raise ValueError("expected the existing relocatable CPython 3.11 distribution")
    dependencies = dependency_closure(ROOT_REQUIREMENTS)
    copies = {}

    def add(name, origin, owner_root):
        safe_relative(name)
        source = origin.resolve(strict=True)
        source.relative_to(owner_root)
        if not source.is_file():
            raise ValueError(f"runtime source is not a file: {name}")
        if name in copies and copies[name] != source:
            raise ValueError(f"runtime collision: {name}")
        copies[name] = source

    # Preserve the existing interpreter/stdlib/dylibs, not the development venv.
    # Internal symlinks are materialized; external links are refused by add().
    for directory in ("lib", "include", "share"):
        for path in (base / directory).rglob("*"):
            if not path.is_file() or any(
                x in path.parts for x in ("site-packages", "__pycache__")
            ):
                continue
            if path.suffix == ".pyc":
                continue
            add("python/" + path.relative_to(base).as_posix(), path, base)
    add("python/bin/python3.11", base / "bin/python3.11", base)
    for path in base.glob("*LICENSE*"):
        add("python/" + path.name, path, base)

    versions = {}
    for name, dist in sorted(dependencies.items()):
        if not dist.files:
            raise ValueError(f"missing installed file inventory: {name}")
        versions[name] = dist.version
        for member in dist.files:
            relative = PurePosixPath(str(member))
            if ".." in relative.parts:
                continue  # console entry points outside site-packages are not invoked
            if (
                "__pycache__" in relative.parts
                or relative.suffix == ".pyc"
                or relative.name == "direct_url.json"
            ):
                continue
            path = Path(dist.locate_file(member))
            add("python/lib/python3.11/site-packages/" + str(relative), path, site)

    # Closed application-code allowlist; models and personal state are elsewhere.
    for path in (ROOT / "runtime").rglob("*.py"):
        add("engine/" + path.relative_to(ROOT).as_posix(), path, ROOT)
    for name in (
        "scripts/verify_snapshot.py",
        "scripts/catalog.py",
        "plugin/runtime_bootstrap.py",
    ):
        add("engine/" + name, ROOT / name, ROOT)
    output.mkdir(parents=True, exist_ok=False)
    files = {}
    for name, source in sorted(copies.items()):
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = sha256(source)
        executable = bool(source.stat().st_mode & 0o111)
        shutil.copyfile(source, target)
        target.chmod(0o755 if executable else 0o644)
        if sha256(target) != digest or sha256(source) != digest:
            raise ValueError(f"source changed during copy: {name}")
        files[name] = {
            "sha256": digest,
            "bytes": target.stat().st_size,
            "executable": executable,
        }
    body = {
        "schema": "aiii.voice.native-runtime",
        "qualified": False,
        "platform": "darwin",
        "arch": "arm64",
        "backend": "mlx",
        "python": "python/bin/python3.11",
        "bootstrap": "engine/plugin/runtime_bootstrap.py",
        "site": "python/lib/python3.11/site-packages",
        "python_version": platform.python_version(),
        "requirements": list(ROOT_REQUIREMENTS),
        "distributions": versions,
        "files": files,
        "scope": "native runtime/code payload; models are host-supplied data, not part of this payload",
    }
    manifest = output / "voice-runtime.json"
    manifest.write_text(json.dumps(body, indent=2) + "\n")
    digest = sha256(manifest)
    verify(output, digest)
    print(
        json.dumps(
            {
                "output": str(output),
                "manifest_sha256": digest,
                "files": len(files),
                "distributions": len(versions),
                "bytes": sum(x["bytes"] for x in files.values()),
            }
        )
    )


def bind_carrier(output, record_path, go):
    """Build a zero-argument entrypoint bound to this exact runtime inventory."""
    from scripts.build_plugin_carrier import PIN, inputs

    manifest_sha = sha256(output / "voice-runtime.json")
    verify(output, manifest_sha)
    profile = json.loads((output / "voice-runtime.json").read_text())
    target = (profile["platform"], profile["arch"])
    if target not in {("darwin", "arm64"), ("windows", "amd64"), ("linux", "amd64")}:
        raise ValueError("runtime carrier target has not been implemented")
    executable = output / (
        "aii-voice-t3.exe" if target[0] == "windows" else "aii-voice-t3"
    )
    if executable.exists() or record_path.exists():
        raise ValueError("carrier build output already exists")
    before = inputs()
    recipe = sha256(Path(__file__))
    version = subprocess.check_output([str(go), "version"], text=True).strip()
    if "go1.27.0 " not in version:
        raise ValueError("runtime carrier requires Go 1.27.0")
    env = {
        **os.environ,
        "GOTOOLCHAIN": "local",
        "GOWORK": "off",
        "GOFLAGS": "",
        "GOPROXY": "off",
        "GOOS": target[0],
        "GOARCH": target[1],
        "CGO_ENABLED": "0",
    }
    command = [
        str(go),
        "build",
        "-trimpath",
        "-buildvcs=false",
        "-ldflags",
        "-X main.packagedRuntimeSHA=" + manifest_sha,
        "-o",
        str(executable),
        ".",
    ]
    subprocess.run(
        command, cwd=ROOT / "plugin/native", env=env, check=True, timeout=180
    )
    if inputs() != before or sha256(Path(__file__)) != recipe:
        raise ValueError("carrier inputs changed during build")
    verify(output, manifest_sha)
    record = {
        "qualified": False,
        "scope": "runtime-bound native payload, not installed/signed",
        "runtime_manifest_sha256": manifest_sha,
        "inputs": before,
        "recipe_sha256": recipe,
        "sdk_revision": PIN["revision"],
        "command": command,
        "toolchain": version,
        "carrier_sha256": sha256(executable),
        "carrier_bytes": executable.stat().st_size,
    }
    with record_path.open("x") as stream:
        json.dump(record, stream, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                k: record[k]
                for k in ("runtime_manifest_sha256", "carrier_sha256", "qualified")
            }
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--verify", metavar="EXPECTED_MANIFEST_SHA256")
    p.add_argument("--bind-carrier", type=Path, metavar="BUILD_RECORD")
    p.add_argument("--go", type=Path, default=Path("/usr/local/go1.27/bin/go"))
    args = p.parse_args()
    if args.bind_carrier:
        bind_carrier(args.output.resolve(), args.bind_carrier.resolve(), args.go)
    elif args.verify:
        verify(args.output.resolve(), args.verify)
        print("native runtime verified")
    else:
        build(args.output.resolve())
