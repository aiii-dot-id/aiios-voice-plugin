import copy

import pytest

from scripts.prove_native_single_read import ORDER, assess


def rows():
    return [
        {
            "arm": arm,
            "seconds": 10 if arm.startswith("A") else 7,
            "identity": {"verified_encoder_tensors": 640},
            "mel_sha256": "same",
            "model": "same",
        }
        for arm in ORDER
    ]


def test_comparison_earns_only_a_complete_runtime_comparison():
    verdict = assess(rows())
    assert verdict["worth_full_runtime_comparison"] and not verdict["promoted"]


@pytest.mark.parametrize("field", ["identity", "mel_sha256", "model"])
def test_changed_identity_is_not_a_speedup(field):
    data = copy.deepcopy(rows())
    data[1][field] = "different"
    with pytest.raises(ValueError, match="different model"):
        assess(data)


def test_incomplete_or_small_gain_does_not_earn_next_gate():
    with pytest.raises(ValueError, match="incomplete"):
        assess(rows()[:-1])
    data = rows()
    for row in data:
        row["seconds"] = 1 if row["arm"].startswith("A") else 0.5
    assert not assess(data)["worth_full_runtime_comparison"]
