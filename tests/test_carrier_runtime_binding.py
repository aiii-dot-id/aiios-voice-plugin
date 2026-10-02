"""The package audit must read a compiled binding, not search executable bytes."""

import os
import shutil
import subprocess

import pytest

from scripts.audit_native_desktop_family import executable_binding


GO = shutil.which("go") or "/usr/local/go1.27/bin/go"
DIGEST = "a" * 64
SOURCE = '''package main
import "fmt"
var packagedRuntimeSHA string
func main() { fmt.Print(packagedRuntimeSHA) }
'''


@pytest.fixture(scope="module")
def carriers(tmp_path_factory):
    directory = tmp_path_factory.mktemp("carrier-binding")
    source = directory / "main.go"
    source.write_text(SOURCE)
    result = {}
    for platform, arch, extension in (
        ("darwin", "arm64", ""),
        ("linux", "amd64", ""),
        ("windows", "amd64", ".exe"),
    ):
        for bound in (True, False):
            target = directory / f"{platform}-{'bound' if bound else 'unbound'}{extension}"
            command = [GO, "build", "-o", str(target)]
            if bound:
                command += ["-ldflags", f"-X main.packagedRuntimeSHA={DIGEST}"]
            command += [str(source)]
            subprocess.run(command, check=True, cwd=directory,
                           env={**os.environ, "GOOS": platform, "GOARCH": arch,
                                "GOTOOLCHAIN": "local", "GOWORK": "off", "GOPROXY": "off"},
                           capture_output=True, timeout=120)
            result[(platform, bound)] = target.read_bytes()
    return result


@pytest.mark.parametrize("platform", ["darwin", "linux", "windows"])
@pytest.mark.parametrize("damage", [None, "unbound", "wrong-binding", "digest-appended-to-unbound"])
def test_actual_executable_binding_not_a_search_hit(carriers, platform, damage):
    raw = carriers[(platform, damage not in ("unbound", "digest-appended-to-unbound"))]
    expected = "f" * 64 if damage == "wrong-binding" else DIGEST
    if damage == "digest-appended-to-unbound":
        raw += expected.encode()
    if damage:
        with pytest.raises(AssertionError, match="compiled runtime binding"):
            executable_binding(raw, expected, go=GO)
    else:
        assert executable_binding(raw, expected, go=GO) == expected
