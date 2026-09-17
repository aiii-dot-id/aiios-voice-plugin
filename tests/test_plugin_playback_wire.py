"""Real SDK transport probes for the engine control; client reports are simulated."""

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.build_plugin_carrier import development_carrier, verify_build
from scripts.plugin_abort_probe import abort_waiting_for_receipt
from scripts.prove_plugin_sdk_engine import SDKHost
from tests.plugin_models import open_args


@pytest.fixture
def carrier():
    candidate = development_carrier()
    if selected := os.environ.get("AII_TEST_CARRIER_BUILD"):
        build = Path(selected).resolve(strict=True)
        verify_build(build)
        candidate = build / candidate.name
    return candidate


def test_sdk_abort_during_receipt_wait_and_successful_reuse(tmp_path, carrier):
    host = SDKHost(
        SimpleNamespace(
            output=tmp_path, fixture=True, carrier=carrier
        )
    )
    try:
        proof = abort_waiting_for_receipt(host, open_args())
        assert proof["abort_admission_seconds"] < 1
        host.call("open", open_args("after-abort"))
        host.event("session_ready", session_id="after-abort")
        host.call(
            "finish_input",
            {"session_id": "after-abort", "stream_id": "mic", "end_sample": 0},
        )
        host.event("input_finished", session_id="after-abort")
        host.call("close", {"session_id": "after-abort", "mode": "drain"})
        assert (
            host.event("session_end", session_id="after-abort")["status"] == "completed"
        )
    finally:
        assert host.close() == 0


def test_missing_receipt_fails_real_sdk_drain_without_inventing_playback(tmp_path, carrier):
    host = SDKHost(
        SimpleNamespace(
            output=tmp_path, fixture=True, carrier=carrier
        )
    )
    try:
        sid = "missing-render-report"
        host.call("open", open_args(sid))
        host.event("session_ready", session_id=sid)
        host.call(
            "synthesize",
            {"session_id": sid, "synthesis_id": "s1", "text": "Complete tail."},
        )
        end = host.event("synthesis_end", "s1", session_id=sid)
        fields = {
            "session_id": sid,
            "synthesis_id": "s1",
            "output_stream": end["output_stream"],
            "rendered_samples": end["delivered_samples"],
            "terminal": False,
        }
        assert host.call("playback_report", fields)["accepted"] is True
        host.call(
            "finish_input", {"session_id": sid, "stream_id": "mic", "end_sample": 0}
        )
        host.event("input_finished", session_id=sid)
        host.call("close", {"session_id": sid, "mode": "drain"})
        deadline = time.monotonic() + 20
        while not any(x["type"] == "failure" for x in host.events):
            assert time.monotonic() < deadline, (
                "missing receipt never became an explicit failure"
            )
            assert host.process.poll() is None, (
                "carrier vanished instead of reporting failure"
            )
            time.sleep(0.01)
        assert not any(x["type"] == "session_end" for x in host.events)
        state = host.call("status", {"session_id": sid})
        assert state["lifecycle"] == "failed"
        assert host.events[-1]["resources_released"] is True
        assert host.events[-1]["playback_verified"] is False
        assert all(
            x.get("terminal") is not True
            for x in host.events
            if x["type"] == "playback_observation"
        )
        with pytest.raises(ValueError, match="STALE_SESSION"):
            host.call("playback_report", dict(fields, terminal=True))
    finally:
        assert host.close() == 0


def test_public_receipts_reject_replaced_session_and_bad_format_without_effect(
    tmp_path, carrier,
):
    host = SDKHost(
        SimpleNamespace(
            output=tmp_path, fixture=True, carrier=carrier
        )
    )
    old = None
    try:
        for sid in ("old", "new"):
            host.call("open", open_args(sid))
            host.event("session_ready", session_id=sid)
            syn = sid + "-speech"
            host.call(
                "synthesize", {"session_id": sid, "synthesis_id": syn, "text": "Ready."}
            )
            end = host.event("synthesis_end", syn, session_id=sid)
            fields = {
                "session_id": sid,
                "synthesis_id": syn,
                "output_stream": end["output_stream"],
                "rendered_samples": end["delivered_samples"],
                "terminal": True,
            }
            if sid == "new":
                assert old is not None
                stable = host.call("status", {"session_id": sid})
                with pytest.raises(ValueError, match="STALE_SESSION"):
                    host.call("playback_report", old)
                with pytest.raises(ValueError, match="PLAYBACK_REPORT"):
                    host.call("playback_report", dict(fields, output_stream=True))
                assert host.call("status", {"session_id": sid}) == stable
            old = fields
            assert host.call("playback_report", fields)["accepted"] is True
            stable = host.call("status", {"session_id": sid})
            assert host.call("playback_report", fields)["accepted"] is True
            assert host.call("status", {"session_id": sid}) == stable
            with pytest.raises(ValueError, match="PLAYBACK_RESOLVED"):
                host.call("playback_report", dict(fields, terminal=False))
            host.call(
                "finish_input", {"session_id": sid, "stream_id": "mic", "end_sample": 0}
            )
            host.event("input_finished", session_id=sid)
            host.call("close", {"session_id": sid, "mode": "drain"})
            assert host.event("session_end", session_id=sid)["status"] == "completed"
    finally:
        assert host.close() == 0
