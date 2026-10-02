"""Build the source-bound native carrier, not a model bundle or installed plugin.

The SDK archive is obtained from the exact commit in plugin/sdk-source.json.
No network fetch, source edit, model load, deployment or signing happens here.
Existing outputs are never overwritten. Both the pre- and post-build inventories
must agree; normal proof entry points refuse stale or tampered default binaries.
"""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PIN_FILE = ROOT / "plugin/sdk-source.json"
PIN = json.loads(PIN_FILE.read_text())
SDK_SOURCE = ROOT / PIN["source"]
BUILD_DIR = ROOT / (".build/native-sdk-" + PIN["revision"][:7])
TARGETS = (
    ("darwin", "arm64", False, "aii-voice-t3"),
    ("darwin", "arm64", True, "aii-voice-t3-race"),
    ("linux", "amd64", False, "aii-voice-t3-linux-amd64"),
    ("windows", "amd64", False, "aii-voice-t3.exe"),
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def regular_files(directory):
    files = {}
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"symbolic source path refused: {path}")
        if path.is_file():
            files[path.relative_to(directory).as_posix()] = digest(path)
        elif not path.is_dir():
            raise ValueError(f"non-regular source path refused: {path}")
    return files


def verify_sdk(root=ROOT):
    pin = json.loads((root / "plugin/sdk-source.json").read_text())
    for key in ("archive", "source"):
        name = PurePosixPath(pin[key])
        if name.is_absolute() or ".." in name.parts:
            raise ValueError("SDK pin path leaves source root")
    archive, source = root / pin["archive"], root / pin["source"]
    if archive.is_symlink() or source.is_symlink():
        raise ValueError("SDK pin cannot resolve through a symlink")
    if digest(archive) != pin["archive_sha256"]:
        raise ValueError("SDK archive digest mismatch")
    expected = {}
    with tarfile.open(archive) as tf:
        seen = set()
        for member in tf:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or member.name in seen:
                raise ValueError("unsafe or duplicate SDK archive path")
            seen.add(member.name)
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError("non-regular SDK archive entry")
            expected[member.name] = hashlib.sha256(tf.extractfile(member).read()).hexdigest()
    if regular_files(source) != expected:
        raise ValueError("SDK checkout differs from sealed archive (including extra files)")
    replace = (
        "replace github.com/aiii-dot-id/aii-plugin-sdk => ../../" + pin["source"]
    )
    mod = (root / "plugin/native/go.mod").read_text()
    if [line for line in mod.splitlines() if line.startswith("replace ")] != [replace]:
        raise ValueError("native go.mod does not bind the pinned SDK")
    return pin, expected


def inputs(root=ROOT):
    pin, sdk_files = verify_sdk(root)
    files = {
        "plugin/native/" + name: sha
        for name, sha in regular_files(root / "plugin/native").items()
    }
    files.update({pin["source"] + "/" + name: sha for name, sha in sdk_files.items()})
    for name in ("plugin/sdk-source.json", "scripts/build_plugin_carrier.py"):
        files[name] = digest(root / name)
    return files


def carrier_path(*, race=False, system=None):
    system = system or sys.platform
    if system == "win32":
        if race:
            raise ValueError("Windows race carrier is not built by the Mac toolchain")
        return BUILD_DIR / "aii-voice-t3.exe"
    if system == "linux":
        if race:
            raise ValueError("Linux race carrier requires its native toolchain")
        return BUILD_DIR / "aii-voice-t3-linux-amd64"
    if system == "darwin":
        return BUILD_DIR / ("aii-voice-t3-race" if race else "aii-voice-t3")
    raise ValueError("unsupported native carrier platform")


def development_carrier():
    """Native Mac race executable; cross-built desktop binaries are plain builds."""
    return carrier_path(race=sys.platform == "darwin")


def verify_build(output=BUILD_DIR, *, root=ROOT):
    record = json.loads((output / "build.json").read_text())
    if record["inputs"] != inputs(root):
        raise ValueError("carrier build is stale: source inputs changed")
    if record["complete"] is not True or record["sdk_revision"] != PIN["revision"]:
        raise ValueError("carrier build incomplete or bound to another SDK")
    if set(record["artifacts"]) != {t[3] for t in TARGETS}:
        raise ValueError("carrier build is missing a target")
    for goos, arch, race, name in TARGETS:
        row = record["artifacts"][name]
        if (row["goos"], row["goarch"], row["race"]) != (goos, arch, race):
            raise ValueError("carrier target binding mismatch")
    actual = regular_files(output)
    expected = {name: value["sha256"] for name, value in record["artifacts"].items()}
    actual.pop("build.json", None)
    if actual != expected:
        raise ValueError("carrier output differs from build manifest")
    return record


def bundle(output, archive):
    """Reviewable carrier/source bundle; not a signed .aiiospkg or model package."""
    record = verify_build(output)
    selected = {ROOT / name for name in record["inputs"]}
    selected.add(ROOT / PIN["archive"])
    selected.update(output.iterdir())
    selected.add(ROOT / "plugin/NATIVE_BUILD.md")
    files = {}
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(selected):
            name = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            files[name] = hashlib.sha256(data).hexdigest()
            zf.writestr(name, data)
        zf.writestr("carrier-bundle-manifest.json", json.dumps(files, indent=2) + "\n")
    verify_build(output)
    with zipfile.ZipFile(archive) as zf:
        if zf.testzip() is not None:
            raise ValueError("carrier bundle CRC mismatch")
        for name, sha in files.items():
            if hashlib.sha256(zf.read(name)).hexdigest() != sha:
                raise ValueError("carrier bundle payload mismatch")
            if digest(ROOT / name) != sha:
                raise ValueError("source changed during carrier packaging")
    print(json.dumps({"archive": str(archive), "sha256": digest(archive),
                      "files": len(files), "scope": "carrier/source only, not installable"}))


def build(go, output):
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise ValueError("this four-artifact builder requires Apple Silicon (native race proof)")
    version = subprocess.check_output([str(go), "version"], text=True).strip()
    if "go1.27.0 " not in version:
        raise ValueError("carrier qualification requires Go 1.27.0")
    before = inputs()
    output.mkdir(parents=True, exist_ok=False)
    record = {
        "schema": "aiii.voice.native-carrier-build",
        "complete": False,
        "qualified": False,
        "scope": "native carrier artifacts only; external model worker and assets required",
        "sdk_revision": PIN["revision"],
        "toolchain": version,
        "inputs": before,
        "artifacts": {},
    }
    try:
        for goos, goarch, race, name in TARGETS:
            env = {**os.environ, "GOTOOLCHAIN": "local", "GOWORK": "off", "GOFLAGS": "",
                   "GOPROXY": "off", "GOOS": goos, "GOARCH": goarch,
                   "CGO_ENABLED": "1" if race else "0"}
            command = [str(go), "build", "-trimpath", "-buildvcs=false"]
            if race:
                command.append("-race")
            command += ["-o", str(output / name), "."]
            subprocess.run(command, cwd=ROOT / "plugin/native", env=env,
                           check=True, timeout=180)
            info = subprocess.check_output([str(go), "version", "-m", str(output / name)], text=True)
            record["artifacts"][name] = {
                "sha256": digest(output / name), "bytes": (output / name).stat().st_size,
                "goos": goos, "goarch": goarch, "race": race, "build_info": info,
            }
        if before != inputs():
            raise ValueError("source changed during carrier build")
        record["complete"] = True
    finally:
        # Preserve an incomplete record too; failed outputs are never promoted.
        (output / "build.json").write_text(json.dumps(record, indent=2) + "\n")
    verify_build(output)
    print(json.dumps({"output": str(output), "artifacts": len(record["artifacts"]),
                      "sdk_revision": PIN["revision"], "qualified": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--go", type=Path, default=Path("/usr/local/go1.27/bin/go"))
    parser.add_argument("--output", type=Path, default=BUILD_DIR)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--bundle", type=Path)
    args = parser.parse_args()
    if args.bundle:
        bundle(args.output.resolve(), args.bundle.resolve())
    elif args.verify:
        verify_build(args.output.resolve())
        print("carrier build verified")
    else:
        build(args.go, args.output.resolve())
