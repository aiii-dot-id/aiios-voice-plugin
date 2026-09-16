import json
from pathlib import Path

from scripts.prove_plugin_long_speech import (
    check_content,
    validate_contract,
    word_errors,
    words,
)


def test_frozen_texts_are_long_distinct_and_cover_multiple_segments():
    contract = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "eval/contracts/windows-long-speech-r1.json"
        ).read_text()
    )
    validate_contract(contract)
    for key in ("first_text", "recovery_text"):
        text = contract[key]
        assert check_content(text, text, contract)["passed"]
        tokens = words(text)
        assert not check_content(text, " ".join(tokens[1:]), contract)["passed"]
        assert not check_content(text, " ".join(tokens[:-1]), contract)["passed"]
        assert not check_content(text, " ".join(tokens[:30] + tokens[45:]), contract)[
            "passed"
        ]


def test_word_edit_distance_counts_each_error_kind():
    assert word_errors(["a", "b", "c"], ["a", "b", "c"]) == 0
    assert word_errors(["a", "b", "c"], ["a", "c"]) == 1
    assert word_errors(["a", "b", "c"], ["a", "x", "c"]) == 1
    assert word_errors(["a", "b", "c"], ["a", "b", "b", "c"]) == 1
