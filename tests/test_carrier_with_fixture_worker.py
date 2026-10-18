import os
import re
import shutil
import subprocess
from pathlib import Path

from scripts.build_plugin_carrier import ROOT, verify_sdk

GO = shutil.which("go") or "/usr/local/go1.27/bin/go"
FIXTURE = "AII_NATIVE_INTERRUPT_FIXTURE"


def composed_tests():
    names = []
    for path in sorted((ROOT / "plugin/native").glob("*_test.go")):
        for function in re.split(r"^func ", path.read_text(encoding="utf-8"), flags=re.M)[1:]:
            name = re.match(r"(Test\w+)\(t \*testing\.T\)", function)
            body = function.split("\n}\n", 1)[0]
            if name and 'os.Getenv("' + FIXTURE + '")' in body:
                names.append(name.group(1))
    return names


def test_the_carrier_and_the_fixture_worker_together():
    composed = composed_tests()
    assert "TestAFailedSettingsReadReachesTheSessionAsTheWayItFailed" in composed and len(composed) >= 3, (
        f"the carrier's tests with the fixture worker were not found in plugin/native: {composed}")
    fixture = Path(os.environ[FIXTURE]).resolve(strict=True)
    verify_sdk()
    env = dict(os.environ, GOWORK="off", GOFLAGS="-buildvcs=false", **{FIXTURE: str(fixture)},
               PATH=str(Path(GO).resolve().parent) + os.pathsep + os.environ.get("PATH", ""))
    run = subprocess.run([GO, "test", "-count=1", "-v", "-run", "^(" + "|".join(composed) + ")$", "."],
                         cwd=ROOT / "plugin/native", capture_output=True, text=True, timeout=600, env=env)
    assert run.returncode == 0, run.stdout + run.stderr
    for name in composed:
        assert "--- PASS: " + name + " " in run.stdout, name + " did not run and pass:\n" + run.stdout
