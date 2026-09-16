import numpy as np
import pytest

from scripts.audit_native_weight_view import NAMES, matrices


class Archive(dict):
    @property
    def files(self):
        return list(self)


def fixture():
    keys = [
        f"{name}/{i}/prompt"
        for name in NAMES
        for i in range(9 if name.startswith("development_") else 1)
    ]
    values = Archive({key: np.ones((1, 7, 1024), dtype="f4") for key in keys})
    return values, [{"id": key, "passed": True} for key in keys]


def test_independent_matrix_audit_requires_all_33():
    values, rows = fixture()
    assert len(matrices(values, values, rows)) == 33
    with pytest.raises(ValueError, match="census"):
        matrices(values, values, rows[:-1])


def test_false_green_does_not_hide_wrong_values():
    reference, rows = fixture()
    actual = Archive({k: v.copy() for k, v in reference.items()})
    actual[rows[0]["id"]] += 0.01
    with pytest.raises(ValueError, match="parity"):
        matrices(reference, actual, rows)


def test_duplicate_row_and_nonfinite_values_fail():
    values, rows = fixture()
    with pytest.raises(ValueError, match="census"):
        matrices(values, values, [rows[0], *rows[:-1]])
    reference = Archive({k: v.copy() for k, v in values.items()})
    values[rows[0]["id"]][0, 0, 0] = np.nan
    with pytest.raises(ValueError, match="finite"):
        matrices(reference, values, rows)
