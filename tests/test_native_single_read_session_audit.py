import copy

import pytest

from scripts.audit_native_single_read_sessions import decision
from scripts.native_single_read_sessions import ORDER


def rows():
    return [
        {
            "name": name,
            "ready_seconds": 50 if name.startswith("baseline") else 47,
            "rtf": [0.5] * 4,
            "first_pcm_ms": [200] * 4,
            "pcm_sha256": ["a", "b", "a", "b"],
            "transcripts": ["all words", "all words"],
        }
        for name in ORDER
    ]


def test_complete_comparison_requires_all_frozen_gates():
    assert decision(rows())["decision"] == "adopt"


@pytest.mark.parametrize(
    "damage", ["startup", "absolute", "rtf", "pcm_delay", "words", "wave"]
)
def test_a_local_improvement_does_not_hide_a_regression(damage):
    data = copy.deepcopy(rows())
    if damage == "absolute":
        for row in data:
            row["ready_seconds"] = 10 if row["name"].startswith("baseline") else 9.5
    else:
        for row in data:
            if row["name"].startswith("candidate"):
                key, value = {
                    "startup": ("ready_seconds", 50),
                    "rtf": ("rtf", [0.6] * 4),
                    "pcm_delay": ("first_pcm_ms", [250] * 4),
                    "words": ("transcripts", ["missing", "words"]),
                    "wave": ("pcm_sha256", ["different"] * 4),
                }[damage]
                row[key] = value
    assert decision(data)["decision"] == "do_not_adopt"


@pytest.mark.parametrize("damage", ["nan", "incomplete_audio", "incomplete_order"])
def test_incomplete_or_nonfinite_evidence_is_refused(damage):
    data = rows()
    if damage == "nan":
        data[0]["ready_seconds"] = float("nan")
    elif damage == "incomplete_audio":
        data[0]["rtf"].pop()
    else:
        data.pop()
    with pytest.raises((ValueError, AssertionError)):
        decision(data)
