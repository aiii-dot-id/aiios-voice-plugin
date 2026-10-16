"""The carrier's Go tests that put it in front of the fixture worker run in a gate.

plugin/native holds tests that start the real carrier with the model-free fixture worker behind
it: the carrier's process and the worker's own code on the wire between them, with a test host on
the public side. They skip unless AII_NATIVE_INTERRUPT_FIXTURE names the fixture worker, and no
gate ran `go test` in plugin/native, so they ran only when someone ran them by hand. One of them
is the test that was missing while the carrier wrote a settings failure's reason under one member
name and the worker read it under another: each side's own test passed.

This runs them with the gate's fixture worker. Which they are is read from the source: every
test function in plugin/native whose own text asks for AII_NATIVE_INTERRUPT_FIXTURE. Each of them
must run and pass; one that is skipped is a failure here, as a failing one is. The rest of the
carrier's Go tests need no fixture worker and are not run here.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

from scripts.build_plugin_carrier import ROOT, verify_sdk

GO = shutil.which("go") or "/usr/local/go1.27/bin/go"
FIXTURE = "AII_NATIVE_INTERRUPT_FIXTURE"


def composed_tests():
    """The Go tests that start the carrier in front of the fixture worker, by name."""
    names = []
    for path in sorted((ROOT / "plugin/native").glob("*_test.go")):
        for function in re.split(r"^func ", path.read_text(encoding="utf-8"), flags=re.M)[1:]:
            name = re.match(r"(Test\w+)\(t \*testing\.T\)", function)
            body = function.split("\n}\n", 1)[0]  # to the end of this function, not into the next one's comment
            if name and 'os.Getenv("' + FIXTURE + '")' in body:
                names.append(name.group(1))
    return names


def test_the_carrier_and_the_fixture_worker_together():
    composed = composed_tests()
    assert "TestAFailedSettingsReadReachesTheSessionAsTheWayItFailed" in composed and len(composed) >= 3, (
        f"the carrier's tests with the fixture worker were not found in plugin/native: {composed}")
    fixture = Path(os.environ[FIXTURE]).resolve(strict=True)
    verify_sdk()  # the carrier builds against the pinned kit source under .build
    # The Go tests build the carrier themselves with the `go` on PATH: the one this test runs.
    env = dict(os.environ, GOWORK="off", GOFLAGS="-buildvcs=false", **{FIXTURE: str(fixture)},
               PATH=str(Path(GO).resolve().parent) + os.pathsep + os.environ.get("PATH", ""))
    run = subprocess.run([GO, "test", "-count=1", "-v", "-run", "^(" + "|".join(composed) + ")$", "."],
                         cwd=ROOT / "plugin/native", capture_output=True, text=True, timeout=600, env=env)
    assert run.returncode == 0, run.stdout + run.stderr
    for name in composed:
        assert "--- PASS: " + name + " " in run.stdout, name + " did not run and pass:\n" + run.stdout
