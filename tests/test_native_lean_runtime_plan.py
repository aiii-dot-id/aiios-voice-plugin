from types import SimpleNamespace

import pytest

from scripts import prove_windows_native_lean_runtime as plan
from scripts.stage_windows_native_lean_runtime import check_runtime_imports


def distribution(name, files, requires=()):
    return SimpleNamespace(metadata={"Name": name}, files=files, requires=requires, version="1.0")


def fixtures(tmp_path, monkeypatch, *, bad_dependency=False, duplicate=False):
    monkeypatch.setattr(plan, "PARENT", tmp_path)
    dists = [distribution(n, [n + "/code.py", n + ".dist-info/METADATA"])
             for n in plan.REQUIREMENTS]
    if bad_dependency:
        dists[0].requires = ["torch"]
    dists += [distribution("torch", ["torch/code.py", "torch/LICENSE", "torch.dll"]),
              distribution("transformers", ["transformers/code.py", "../outside.py"])]
    if duplicate:
        dists.append(dists[0])
    monkeypatch.setattr(plan.metadata, "distributions", lambda path: dists if path == [str(tmp_path / "deps/tts")] else [])
    files = {"deps/tts/" + n: {"sha256": "f" * 64, "bytes": 5}
             for d in dists for n in d.files if ".." not in n}
    files["unowned/keep.txt"] = {"sha256": "e" * 64, "bytes": 9}
    return {"site": "deps/tts", "files": files}


def test_plan_keeps_roots_licenses_and_unowned_files(tmp_path, monkeypatch):
    report = plan.dependency_plan(fixtures(tmp_path, monkeypatch))
    assert set(report["resolved"]) == set(plan.REQUIREMENTS)
    assert set(report["removed_files"]) == {"deps/tts/torch/code.py", "deps/tts/torch.dll", "deps/tts/transformers/code.py"}
    assert report["removed_file_count"] == 3 and report["removed_bytes"] == 15


def test_native_root_cannot_reintroduce_python_torch(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="still requires"):
        plan.dependency_plan(fixtures(tmp_path, monkeypatch, bad_dependency=True))


def test_ambiguous_distribution_refused(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="ambiguous"):
        plan.dependency_plan(fixtures(tmp_path, monkeypatch, duplicate=True))


def test_independent_copy_preserves_bytes_and_never_overwrites(tmp_path):
    src, target = tmp_path / "source", tmp_path / "new/file"
    src.write_bytes(b"bound bytes")
    plan.copy_owned((src, target))
    assert target.read_bytes() == src.read_bytes() == b"bound bytes"
    assert target.stat().st_ino != src.stat().st_ino
    with pytest.raises(FileExistsError):
        plan.copy_owned((src, target))
    assert target.read_bytes() == src.read_bytes() == b"bound bytes"


@pytest.mark.parametrize("missing", ["runtime/helper.py", "runtime/nested.py"])
def test_freeze_rejects_missing_direct_or_transitive_helpers(missing):
    source = {"plugin/bootstrap.py": b"from runtime.helper import value\n",
              "runtime/helper.py": b"from runtime.nested import value\n",
              "runtime/nested.py": b"value = 1\n"}
    check_runtime_imports(source, ["plugin/bootstrap.py"])
    del source[missing]
    with pytest.raises(ValueError, match="missing internal import"):
        check_runtime_imports(source, ["plugin/bootstrap.py"])
