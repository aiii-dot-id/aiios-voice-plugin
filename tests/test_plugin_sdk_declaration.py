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
    directory = os.environ.get("AII_TEST_CARRIER_DIR")
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
    assert set(operations) == expected and len(operations) == len(expected)
    assert "AII_VOICE_READY" not in result.stderr, "Describe must not load models"


def test_proof_binds_the_actual_carrier_declaration(carrier):
    declaration = describe_carrier(carrier)
    assert len(declaration["carrier_sha256"]) == 64
    validate_description(declaration["operations"])


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
