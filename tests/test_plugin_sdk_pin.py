"""The carrier's pinned SDK keeps every admitted control's reply under an
engine-event backlog, so a backlog of events cannot displace an answer and
fault the speech session, and its Session.Flush confirms that accepted frames
were written, which the carrier relies on before exiting after a worker
failure. This runs the SDK's own regressions against the exact sealed source
the carrier builds from; a missing test is a failure."""
import os
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


def test_pinned_sdk_preserves_control_replies_under_event_backlog():
    pin, _ = verify_sdk()
    run = subprocess.run([GO, "test", "-count=1", "-v", "-run", "^(" + "|".join(REGRESSIONS) + ")$", "./pkg/aiiosdk"],
                         cwd=ROOT / pin["source"], capture_output=True, text=True, timeout=600,
                         env=dict(os.environ, GOWORK="off", GOFLAGS="-buildvcs=false"))
    assert run.returncode == 0, run.stdout + run.stderr
    for name in REGRESSIONS:
        assert "--- PASS: " + name + " " in run.stdout, name + " did not run and pass"
