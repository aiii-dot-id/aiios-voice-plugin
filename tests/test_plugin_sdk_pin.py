import json
import os
import shutil
import subprocess

from scripts.build_plugin_carrier import ROOT, verify_sdk

from scripts import go_toolchain
GO = shutil.which("go") or str(go_toolchain.default_go())
REGRESSIONS = ("TestEventBacklogPreservesEveryAdmittedControlReply",
               "TestFailedAdmissionWriteReleasesItsReservedSlot",
               "TestVisibleReplyAlreadyFreedItsAdmissionReservation",
               "TestFlushReturnsOnlyOnceEveryAcceptedFrameIsWritten",
               "TestFlushDoesNotWaitForFramesAcceptedAfterItBegan",
               "TestFlushReportsTheFaultThatStoppedItsFrames",
               "TestFlushReportsALaneThatEndsWithFramesStillQueued",
               "TestACancelledFlushIsAnUnknownOutcomeAndLeavesNothingBehind",
               "TestAWithdrawnHostCallDoesNotHoldFlush",
               "TestAFlushBesideTheFirstFrameIsRaceFree",
               "TestFlushIsSafeBesideConcurrentEventsAnswersAndHostCalls",
               "TestAPendingFlushTakesNoReservedReplyCapacity")


def test_pinned_sdk_preserves_control_replies_under_event_backlog():
    pin, _ = verify_sdk()
    source = ROOT / pin["source"]
    if not list((source / "pkg/aiiosdk").glob("*_test.go")):
        proven = json.loads((ROOT / "tests/vectors/sdk_regressions.json").read_text())
        assert proven["pinned_revision"] == pin["revision"], \
            "the pinned SDK carries no tests and no run of the regressions is recorded for it (tests/vectors/sdk_regressions.json)"
        for package, digest in proven["code_sha256"].items():
            run = subprocess.run([GO, "run", str(ROOT / "scripts/sdk_code_digest.go"), str(source / package)],
                                 cwd=ROOT, capture_output=True, text=True, timeout=600, env=dict(os.environ, GOWORK="off"))
            assert run.returncode == 0, run.stdout + run.stderr
            assert run.stdout.split()[0] == digest, package + " of the pinned SDK is not the code the regressions passed on"
        return
    run = subprocess.run([GO, "test", "-count=1", "-v", "-run", "^(" + "|".join(REGRESSIONS) + ")$", "./pkg/aiiosdk"],
                         cwd=ROOT / pin["source"], capture_output=True, text=True, timeout=600,
                         env=dict(os.environ, GOWORK="off", GOFLAGS="-buildvcs=false"))
    assert run.returncode == 0, run.stdout + run.stderr
    for name in REGRESSIONS:
        assert "--- PASS: " + name + " " in run.stdout, name + " did not run and pass"


def test_tree_vectors_stand_only_for_the_proven_sdk_code(tmp_path):
    from scripts.sdk_vectors import vector
    pin, _ = verify_sdk()
    source = ROOT / pin["source"]
    proven = json.loads((ROOT / "tests/vectors/sdk_regressions.json").read_text())
    for name in proven["vectors_sha256"]:
        got = vector(source, name, GO)
        assert got is not None, name
        if not (source / "vectors" / name).is_file():
            assert got == (ROOT / "tests/vectors" / name).read_bytes()
    if (source / "vectors").is_dir():
        return
    changed = tmp_path / "sdk"
    shutil.copytree(source, changed, symlinks=True)
    victim = sorted((changed / "pkg/aiiosdk").glob("*.go"))[0]
    victim.write_text(victim.read_text() + "\nvar _ = 1\n")
    assert vector(changed, "runtime_extent.json", GO) is None
