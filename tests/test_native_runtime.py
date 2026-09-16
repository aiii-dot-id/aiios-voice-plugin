import json
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.runtime_bootstrap import module_files
from scripts.package_native_runtime import (
    dependency_closure,
    runtime_inventory,
    safe_relative,
    sha256,
    verify,
)


def test_synthetic_module_attribute_is_not_a_file(tmp_path):
    module = types.ModuleType("synthetic")

    def invented(name):
        raise AssertionError("module attribute hook must not be called")

    module.__getattr__ = invented
    assert module_files(tmp_path, {"synthetic": module}) == {}
    real = types.ModuleType("real")
    real.__file__ = str(tmp_path / "real.py")
    assert module_files(tmp_path, {"real": real}) == {"real": "real.py"}
    real.__file__ = str(tmp_path.parent / "foreign.py")
    with pytest.raises(ValueError):
        module_files(tmp_path, {"real": real})


@pytest.mark.parametrize(
    "name,label", [("torch.ops", "_ops.py"), ("torch.classes", "_classes.py")]
)
def test_torch_facade_is_bound_to_its_real_packaged_implementation(
    tmp_path, name, label
):
    package = tmp_path / "deps/torch"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / label).write_text("")
    modules = {
        "torch": SimpleNamespace(__file__=str(package / "__init__.py")),
        "torch." + label.removesuffix(".py"): SimpleNamespace(
            __file__=str(package / label)
        ),
        name: SimpleNamespace(__file__=label),
    }
    assert module_files(tmp_path, modules)[name] == str(Path("deps/torch") / label)
    modules[name].__file__ = "other.py"
    with pytest.raises(ValueError, match="facade source label"):
        module_files(tmp_path, modules)
    modules[name].__file__ = label
    modules["torch." + label.removesuffix(".py")].__file__ = str(
        package / "__init__.py"
    )
    with pytest.raises(ValueError, match="implementation differs"):
        module_files(tmp_path, modules)
    del modules["torch." + label.removesuffix(".py")]
    with pytest.raises(ValueError, match="no implementation owner"):
        module_files(tmp_path, modules)


def test_dependency_closure_honors_markers_extras_and_constraints():
    dist = {
        "base": SimpleNamespace(
            version="1", requires=["extra[feature]>=2", 'absent; python_version < "2"']
        ),
        "extra": SimpleNamespace(version="2", requires=['leaf; extra == "feature"']),
        "leaf": SimpleNamespace(version="3", requires=[]),
    }
    assert set(dependency_closure(["base"], dist.__getitem__)) == {
        "base",
        "extra",
        "leaf",
    }
    with pytest.raises(ValueError, match="conflicts"):
        dependency_closure(["extra>=9"], dist.__getitem__)
    with pytest.raises(KeyError):
        dependency_closure(["missing"], dist.__getitem__)


@pytest.mark.parametrize(
    "name", ["", ".", "C:a", "a:stream", "/a", "../a", "a/../b", "a\\b", "a//b", "./a"]
)
def test_payload_path_refuses_escape(name):
    with pytest.raises(ValueError):
        safe_relative(name)


def test_inventory_detects_tamper_extra_and_links(tmp_path):
    code = tmp_path / "engine.py"
    code.write_text("pass\n")
    manifest = tmp_path / "voice-runtime.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "aiii.voice.native-runtime",
                "qualified": False,
                "platform": "darwin",
                "files": runtime_inventory(tmp_path),
            }
        )
    )
    digest = sha256(manifest)
    verify(tmp_path, digest)
    code.write_text("fail\n")
    with pytest.raises(ValueError, match="differs"):
        verify(tmp_path, digest)
    code.write_text("pass\n")
    extra = tmp_path / "extra.py"
    extra.write_text("pass")
    with pytest.raises(ValueError, match="differs"):
        verify(tmp_path, digest)
    extra.unlink()
    extra.symlink_to(code)
    with pytest.raises(ValueError, match="symlink"):
        verify(tmp_path, digest)


def test_model_data_never_supplies_catalog(tmp_path):
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    (tmp_path / "catalog.py").write_text('raise AssertionError("data became code")')
    source = f"import sys; sys.path[:0]=[{str(tmp_path)!r},{str(root)!r}]; import scripts.verify_snapshot; print(scripts.verify_snapshot.utc_now.__module__)"
    result = subprocess.run(
        [sys.executable, "-I", "-c", source], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "scripts.catalog"


def test_runtime_resume_reuses_only_exact_bytes(tmp_path):
    from scripts.package_windows_runtime import copy_checked

    source, target = tmp_path / "source", tmp_path / "output/file"
    source.write_bytes(b"bound native bytes")
    row, reused = copy_checked(source, target)
    assert not reused and row["sha256"] == sha256(source)
    assert copy_checked(source, target) == (row, True)
    target.write_bytes(b"partial copy")
    with pytest.raises(ValueError, match="copy/source changed"):
        copy_checked(source, target)
    assert target.read_bytes() == b"partial copy", (
        "resume must not hide inconsistent bytes"
    )
