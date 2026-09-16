"""Retained SDK facts, including deliberate false completion/receipt claims."""

import copy
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.audit_plugin_abort_drain import check
from scripts.build_plugin_carrier import development_carrier, verify_build
from scripts.plugin_abort_probe import abort_waiting_for_receipt
from scripts.prove_plugin_sdk_engine import SDKHost
from tests.plugin_models import open_args


@pytest.fixture(scope="module")
def evidence(tmp_path_factory):
    output = tmp_path_factory.mktemp("sdk-abort-audit")
    carrier = development_carrier()
    if selected := os.environ.get("AII_TEST_CARRIER_BUILD"):
        build = Path(selected).resolve(strict=True)
        verify_build(build)
        carrier = build / carrier.name
    host = SDKHost(
        SimpleNamespace(
            output=output, fixture=True, carrier=carrier
        )
    )
    try:
        row = abort_waiting_for_receipt(host, open_args())
        pcm = (output / row["pcm_file"]).read_bytes()
    finally:
        assert host.close() == 0
    return row, pcm


def test_abort_auditor_accepts_executed_sdk_facts(evidence):
    row, pcm = evidence
    assert check(row, pcm)["playback_unobserved"] is True


@pytest.mark.parametrize(
    "mutation",
    ["completed", "acoustic", "rendered", "refused", "no_drain", "no_end", "foreign"],
)
def test_abort_auditor_refuses_false_evidence(evidence, mutation):
    original, pcm = evidence
    row = copy.deepcopy(original)
    if mutation == "completed":
        row["events"][-1]["status"] = "completed"
    elif mutation == "acoustic":
        row["events"][-1]["playback_verified"] = True
    elif mutation == "rendered":
        row["final_snapshot"]["playback"]["rendered_samples"] = row["delivered_samples"]
    elif mutation == "refused":
        next(
            c
            for c in row["calls"]
            if c["operation"] == "close" and c["arguments"]["mode"] == "abort"
        )["reply"] = {"error": "SESSION_STATE"}
    elif mutation == "no_drain":
        row["calls"] = [
            c
            for c in row["calls"]
            if not (c["operation"] == "close" and c["arguments"]["mode"] == "drain")
        ]
    elif mutation == "no_end":
        row["frames"].pop()
    elif mutation == "foreign":
        row["events"][-1]["session_id"] = "replacement"
    with pytest.raises(AssertionError):
        check(row, pcm)
