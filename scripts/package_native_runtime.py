"""Verify a native runtime against its bound inventory, and bind a carrier to it.

A runtime is a directory whose profile, voice-runtime.json, binds every file
in it by SHA-256. verify() holds a runtime to its profile; bind_carrier()
builds the zero-argument carrier that is bound to that profile. No model copy,
download or deployment occurs here, and a bound carrier is not a signed package.

This module also packed a runtime once: build() froze a CPython interpreter,
its site-packages and the Python engine into one, under a profile that named
the interpreter and a bootstrap script. That packer is removed. The engine is
the native worker, and nothing here packs another or binds a carrier to one.
verify() still holds such a runtime to its profile: whether the bytes of a
runtime that exists are intact is a question, not a way to start, ship or
build it.
"""

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath

from scripts.runtime_limits import profile_limits

ROOT = Path(__file__).resolve().parents[1]

# NOTHING IS MADE OF A PROFILE THAT DESCRIBES AN INTERPRETER. A Python engine's
# profile named an interpreter, a bootstrap script and a site directory in
# these three members; a native profile leaves them out or states them empty.
# The carrier refuses such a profile at its start (plugin/native/runtime.go,
# pythonProfileRefused), and nothing packs one any more.
#
# verify() does not refuse one. It answers whether a runtime's bytes are what
# its profile binds, and that may be asked of an old runtime too. What refuses
# is everything that would make something of it, each where it reads the
# profile it would build on: bind_carrier() below, and the scripts that
# rebuild, stage, rebind, restore, package or assemble a runtime.
INTERPRETER_MEMBERS = ("python", "bootstrap", "site")


def refuse_interpreter_profile(profile):
    """Raise for a parsed profile that describes an interpreter; a native profile passes."""
    stated = [name for name in INTERPRETER_MEMBERS if profile.get(name) not in (None, "")]
    if stated:
        raise ValueError(
            "this runtime profile describes a Python engine (it states " + ", ".join(stated)
            + "), and no engine is one: the carrier starts only the native worker a profile "
            "names, and no script here rebuilds, stages, binds or assembles another")


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


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
    # A table of time limits the carrier would refuse at its start is refused
    # by every script that reads the profile. One from before the table was
    # stated is still read here: a parent to rebuild from, a stage to recover.
    # Staging (stage_qualified_runtime) is where a profile must state it.
    profile_limits(body, released=False)
    for name in body["files"]:
        safe_relative(name)
    if runtime_inventory(root, target_platform=body["platform"]) != body["files"]:
        raise ValueError("runtime payload differs from bound inventory")
    return body


def bind_carrier(output, record_path, go):
    """Build a zero-argument entrypoint bound to this exact runtime inventory."""
    from scripts.build_plugin_carrier import PIN, inputs

    manifest_sha = sha256(output / "voice-runtime.json")
    verify(output, manifest_sha)
    profile = json.loads((output / "voice-runtime.json").read_text())
    # No carrier is bound to a runtime that describes an interpreter: refused
    # before any toolchain is run, and nothing is written.
    refuse_interpreter_profile(profile)
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
        # With neither option this packed a Python engine's runtime. It packs nothing now.
        p.error("say --verify or --bind-carrier: this tool no longer packs a runtime")
