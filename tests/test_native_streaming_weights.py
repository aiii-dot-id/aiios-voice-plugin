import numpy as np
import pytest

from scripts.compare_native_streaming_weights import linear_name, lstm_unmap, verdict


def test_lstm_unpack_gate_order_and_bias_halves():
    packed = np.array([1, 2, 7, 8, 3, 4, 5, 6], dtype=np.float32)
    assert lstm_unmap(packed).tolist() == list(range(1, 9))
    with pytest.raises(ValueError):
        lstm_unmap(np.ones(7))


def test_consumer_mapping_not_shape_guess():
    assert (
        linear_name("encoder", "/layers.23/self_attn/linear_k/MatMul")
        == "encoder.layers.23.self_attn.linear_k.weight"
    )
    assert (
        linear_name("encoder", "/prompt_kernel/prompt_kernel.0/MatMul")
        == "prompt_kernel.0.weight"
    )
    assert (
        linear_name("joiner", "/joint_net/joint_net.2/MatMul")
        == "joint.joint_net.2.weight"
    )
    with pytest.raises(ValueError):
        linear_name("encoder", "/unrecognized/MatMul")


@pytest.mark.parametrize("actual", [{}, {"a": 2}, {"a": 1, "b": 1}])
def test_census_refuses_missing_changed_or_extra(actual):
    assert not verdict({"a": 1}, actual)["passed"]


def test_exact_census_passes():
    assert verdict({"a": 1}, {"a": 1}) == {
        "passed": True,
        "missing": [],
        "extra": [],
        "mismatched": [],
        "matched": 1,
    }
