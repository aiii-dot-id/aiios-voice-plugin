import hashlib

import numpy as np
import pytest

from scripts.audit_native_preview_geometry import compare, sha


def values():
    return {
        f"{context}/{case}/{i}": np.ones((1, context + 1, 1024), "f4")
        for context, count in ((6, 9), (0, 63), (3, 15))
        for case in ("development_normal", "development_quiet", "development_very_quiet")
        for i in range(count)
    }


def test_complete_equal_census_passes():
    ref = values()
    result = compare(ref, ref)
    assert result["passed"]
    assert len(result["comparisons"]) == 261
    assert not result["promoted"]


def test_absent_chunk_cannot_improve_report():
    ref, candidate = values(), values()
    del candidate["3/development_quiet/14"]
    with pytest.raises(ValueError, match="census"):
        compare(ref, candidate)


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_nonfinite_refused(bad):
    ref, candidate = values(), values()
    candidate["0/development_normal/0"][0, 0, 0] = bad
    with pytest.raises(ValueError, match="nonfinite"):
        compare(ref, candidate)


def test_single_bad_value_fails_even_when_overall_error_is_small():
    ref, candidate = values(), values()
    candidate["6/development_normal/0"][0, 0, 0] += 0.003
    result = compare(ref, candidate)
    assert result["passed"] is False
    assert sum(not r["passed"] for r in result["comparisons"]) == 1


def test_many_small_errors_fail_relative_limit():
    ref, candidate = values(), values()
    candidate["3/development_normal/0"] += 0.001
    assert compare(ref, candidate)["passed"] is False


def test_hash_accepts_path_and_file_attribute_string(tmp_path):
    path = tmp_path / "source.py"
    path.write_bytes(b"real source")
    assert sha(path) == sha(str(path)) == hashlib.sha256(b"real source").hexdigest()
