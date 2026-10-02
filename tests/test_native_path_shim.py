from pathlib import Path
import pytest
import json
import hashlib
import shutil
import subprocess
from scripts.stage_native_path_shim import PINS, UPSTREAM, derive, main


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


def test_cli_emits_exact_binding_and_refuses_replacement(tmp_path):
    output = tmp_path / "derived"
    args = ["--upstream", str(UPSTREAM), "--output", str(output)]
    main(args)
    binding = json.loads((output / "path-shim-binding.json").read_text())
    assert binding["upstream"] == PINS
    assert set(binding["candidate"]) == {"paths.h", *(Path(n).name for n in PINS)}
    for name, digest in binding["candidate"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest
    with pytest.raises(FileExistsError):
        main(args)


def test_missing_input_is_refused_before_output(tmp_path):
    output = tmp_path / "derived"
    with pytest.raises(FileNotFoundError):
        main(["--upstream", str(tmp_path), "--output", str(output)])
    assert not output.exists()


def test_windows_resident_refuses_omitted_path_shim(tmp_path):
    cmake = shutil.which("cmake")
    if not cmake:
        pytest.skip("CMake is required for the configure refusal probe")
    root = Path(__file__).resolve().parents[1]
    command = [cmake, "-S", str(root / "runtime/native_pocket/windows_resident"),
               "-B", str(tmp_path / "build")]
    command += [f"-D{name}={tmp_path / 'unused'}" for name in (
        "ENGINE_SOURCE", "ENGINE_BUILD", "RESIDENT_SOURCE", "MIMI_OVERRIDE",
        "ACOUSTIC_OVERRIDE", "BOUND_SOURCE", "VULKAN_LIBRARY")]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert "Missing PATH_SHIM_SOURCE" in result.stdout + result.stderr
