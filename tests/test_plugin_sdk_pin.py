"""The carrier's pinned SDK keeps every admitted control's reply under an
engine-event backlog, so a backlog of events cannot displace an answer and
fault the speech session, and its Session.Flush confirms that accepted frames
were written, which the carrier relies on before exiting after a worker
failure. The retained regressions are compiler-overlaid onto the exact sealed
public production source. The dependency archive stays unchanged; a missing
test is a failure."""
import os
import json
import shutil
import subprocess

from scripts.build_plugin_carrier import ROOT, verify_sdk

GO = shutil.which("go") or "/usr/local/go1.27/bin/go"
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


def test_pinned_sdk_preserves_control_replies_under_event_backlog(tmp_path):
    pin, before = verify_sdk()
    fixtures = sorted((ROOT / "tests/sdk_regressions").glob("*_test.go"))
    assert len(fixtures) == 4
    overlay = tmp_path / "sdk-tests-overlay.json"
    overlay.write_text(json.dumps({"Replace": {
        str(ROOT / pin["source"] / "pkg/aiiosdk" / ("voice_" + p.name)): str(p)
        for p in fixtures
    }}))
    run = subprocess.run([GO, "test", "-overlay", str(overlay), "-race", "-count=1", "-v", "-run", "^(" + "|".join(REGRESSIONS) + ")$", "./pkg/aiiosdk"],
                         cwd=ROOT / pin["source"], capture_output=True, text=True, timeout=600,
                         env=dict(os.environ, GOWORK="off", GOFLAGS="-buildvcs=false"))
    assert run.returncode == 0, run.stdout + run.stderr
    for name in REGRESSIONS:
        assert "--- PASS: " + name + " " in run.stdout, name + " did not run and pass"
    assert verify_sdk() == (pin, before), "SDK production source changed"
