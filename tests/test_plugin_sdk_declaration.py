"""The real carrier must advertise the shared kit's complete resident contract."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts.build_plugin_carrier import development_carrier, verify_build
from scripts.prove_plugin_sdk_engine import describe_carrier, validate_description


@pytest.fixture
def carrier():
    directory = os.environ.get("AII_TEST_CARRIER_BUILD")
    if directory is None:
        return development_carrier()
    output = Path(directory).resolve()
    verify_build(output)
    return output / development_carrier().name


def test_carrier_declares_playback_control_without_starting_a_worker(carrier):
    result = subprocess.run(
        [str(carrier)],
        env={**os.environ, "AIISDK_DESCRIBE": "1"},
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    operations = [item["id"] for item in json.loads(result.stdout)]
    expected = {
        "speech.session." + name
        for name in (
            "open",
            "synthesize",
            "cancel_synthesis",
            "stop_playback",
            "finish_input",
            "close",
            "status",
            "playback_report",
        )
    }
    expected |= {"speaker." + name for name in
                 ("list", "enroll", "remove", "reset", "discard_capture", "upgrade_policy", "buckets", "associate", "forget")}
    assert set(operations) == expected and len(operations) == len(expected)
    assert "AII_VOICE_READY" not in result.stderr, "Describe must not load models"


def test_proof_binds_the_actual_carrier_declaration(carrier):
    declaration = describe_carrier(carrier)
    assert len(declaration["carrier_sha256"]) == 64
    validate_description(declaration["operations"])
    root = Path(__file__).resolve().parents[1] / "plugin/native"
    for row in declaration["operations"]:
        if row["id"].startswith("speaker."):
            for field in ("input", "output"):
                schema = json.loads((root / row[field]).read_text())
                assert schema["type"] == "object"
                assert schema["properties"], "empty, undiscoverable speaker contract"


@pytest.mark.parametrize(
    "damage", ["seven", "duplicate", "wrong", "nonstring", "object"]
)
def test_declaration_gate_rejects_incomplete_or_ambiguous_wire(damage):
    rows = [
        {"id": "speech.session." + name}
        for name in (
            "open",
            "synthesize",
            "cancel_synthesis",
            "stop_playback",
            "finish_input",
            "close",
            "status",
            "playback_report",
        )
    ]
    if damage == "seven":
        rows.pop()
    elif damage == "duplicate":
        rows.append(rows[-1])
    elif damage == "wrong":
        rows[-1]["id"] = "private.playback"
    elif damage == "nonstring":
        rows[-1]["id"] = []
    else:
        rows = {}
    with pytest.raises(ValueError):
        validate_description(rows)


@pytest.mark.parametrize("damage", ["missing-guided", "missing-upgrade", "missing-input",
                                   "missing-output", "no-confirmation", "wrong-effects",
                                   "wrong-capabilities", "blank-summary", "object-id"])
def test_complete_carrier_contract_is_load_bearing(carrier, damage):
    rows = describe_carrier(carrier)["operations"]
    enroll = next(row for row in rows if row["id"] == "speaker.enroll")
    if damage == "missing-guided": rows = [r for r in rows if r["id"] != "speaker.discard_capture"]
    elif damage == "missing-upgrade": rows = [r for r in rows if r["id"] != "speaker.upgrade_policy"]
    elif damage == "missing-input": enroll.pop("input")
    elif damage == "missing-output": enroll.pop("output")
    elif damage == "no-confirmation": enroll["operator_confirms"] = False
    elif damage == "wrong-effects": enroll["effects"] = "read.internal"
    elif damage == "wrong-capabilities": enroll["capabilities"] = []
    elif damage == "blank-summary": enroll["summary"] = " "
    elif damage == "object-id": enroll["id"] = {}
    with pytest.raises(ValueError): validate_description(rows)
