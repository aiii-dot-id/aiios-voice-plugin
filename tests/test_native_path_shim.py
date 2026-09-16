from pathlib import Path
import pytest
from scripts.stage_native_path_shim import PINS, UPSTREAM, derive


def test_only_declared_canonicalization_sites_change():
    before = {n: (UPSTREAM / n).read_bytes() for n in PINS}
    after = derive(before)
    assert set(after) == {Path(n).name for n in before}
    assert sum(b.count(b"aii::platform::existing_io_path(path)") for b in after.values()) == 7
    assert sum(b.count(b"aii::platform::physical_key(path)") for b in after.values()) == 1
    for n, old in before.items():
        raw = after[Path(n).name]
        restored = raw.removeprefix(b'#include "paths.h"\n').replace(
            b"aii::platform::physical_key(path)", b"std::filesystem::weakly_canonical(path).generic_string()"
        ).replace(b"aii::platform::existing_io_path(path)", b"std::filesystem::weakly_canonical(path)")
        assert restored == old


def test_wrong_upstream_or_edited_model_code_is_refused():
    before = {n: (UPSTREAM / n).read_bytes() for n in PINS}
    before[next(iter(before))] += b"\n"
    with pytest.raises(ValueError, match="binding differs"):
        derive(before)
