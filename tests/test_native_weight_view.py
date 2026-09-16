import pytest

from scripts.prepare_native_weight_view import layout, validate_header


def test_linear_transpose_is_explicit_and_wrong_orientation_refused():
    assert layout({"shape": [2, 3]}, [3, 2], "transpose") is True
    assert layout({"shape": [2, 3]}, [2, 3], "identity") is False
    with pytest.raises(ValueError, match="layout"):
        layout({"shape": [2, 3]}, [2, 3], "transpose")
    with pytest.raises(ValueError, match="layout"):
        layout({"shape": [2, 3]}, [3, 2], "identity")


def test_reference_header_is_contiguous_and_complete():
    header = {
        str(i): {"dtype": "F32", "shape": [1], "data_offsets": [4 * i, 4 * i + 4]}
        for i in range(655)
    }
    validate_header(header, 2620)
    header["7"]["data_offsets"] = [0, 4]
    with pytest.raises(ValueError, match="Overlapping"):
        validate_header(header, 2620)


def test_wrong_precision_is_not_silently_reinterpreted():
    with pytest.raises(ValueError, match="FP32"):
        validate_header(
            {"x": {"dtype": "BF16", "shape": [1], "data_offsets": [0, 2]}}, 2
        )
