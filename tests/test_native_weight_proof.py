import numpy as np

from scripts.prove_native_weight_view import difference, words


def test_numerical_gate_rejects_shape_nan_and_real_error():
    reference = np.ones((1, 7, 1024), dtype="f4")
    assert difference(reference, reference.copy())["passed"]
    assert not difference(reference, reference.transpose(0, 2, 1))["passed"]
    assert not difference(reference, reference * np.nan)["passed"]
    assert not difference(reference, reference + 0.001)["passed"]
    large = reference * 100
    assert not difference(large, large + 0.003)["passed"]


def test_opening_and_last_words_cannot_disappear():
    expected = words("Please keep the opening words cobalt lantern seventeen.")
    assert words("please keep the opening words cobalt lantern seventeen") == expected
    assert words("keep the opening words cobalt lantern seventeen") != expected
    assert words("Please keep the opening words cobalt lantern") != expected
