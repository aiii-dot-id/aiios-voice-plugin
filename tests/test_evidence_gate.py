"""The evidence gate names exactly what a test file needs and the tree lacks."""

from types import SimpleNamespace

import pytest

import tests.conftest as gate
from tests.conftest import ROOT, missing_needs, skip_unless_shipped


def tree(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "present.py").write_text("")
    (tmp_path / "tests").mkdir()
    return tmp_path


def test_gate_names_absent_needs_and_keeps_present_ones(tmp_path):
    root = tree(tmp_path)
    needy = root / "tests" / "test_needy.py"
    needy.write_text(
        "from scripts.present import a\nfrom scripts.absent import b\n"
        "from scripts import present as p, gone as g\n"
        "import scripts.dotted.inner\n"
        "P = ROOT / 'deliverables/run-1/result.json'\n"
    )
    assert missing_needs(needy, root) == [
        "scripts.absent", "scripts.gone", "scripts.dotted", "deliverables/",
    ]
    satisfied = root / "tests" / "test_satisfied.py"
    satisfied.write_text("from scripts.present import a\nfrom scripts import present\n")
    assert missing_needs(satisfied, root) == []
    (root / "deliverables").mkdir()
    assert missing_needs(needy, root) == ["scripts.absent", "scripts.gone", "scripts.dotted"]


def test_only_what_runs_at_import_leaves_a_file_out(tmp_path):
    root = tree(tmp_path)
    mixed = root / "tests" / "test_mixed.py"
    mixed.write_text(
        "import pytest\n"
        "if True:\n    from scripts.guarded import g\n"  # indented, but runs at import
        "class Suite:\n    from scripts.in_class import c\n"
        "    def test_method(self):\n        from scripts.in_method import m\n"
        "@pytest.mark.parametrize('p', [ROOT / 'artifacts/decorated'])\n"
        "def test_body(p, default=ROOT / 'artifacts/default'):\n"
        "    from scripts.in_body import b\n"
        "    import scripts.also_in_body\n"
        "    return ROOT / 'deliverables/read-in-body.json'\n"
        "def helper():\n    from scripts.in_helper import h\n"
        "    return ROOT / 'deliverables/read-in-helper.json'\n"
        "async def test_async():\n    from scripts.in_async import a\n"
        "later = lambda: __import__('scripts.in_lambda')\n"
    )
    assert missing_needs(mixed, root) == ["scripts.guarded", "scripts.in_class", "artifacts/"]


def test_gate_follows_sibling_test_modules_in_every_import_form(tmp_path):
    root = tree(tmp_path)
    (root / "tests" / "test_base.py").write_text("from scripts.absent import b\n")
    (root / "tests" / "test_fine.py").write_text("X = 1\n")
    (root / "tests" / "test_body_only.py").write_text(
        "def test_x():\n    from scripts.absent import b\n"
    )
    user = root / "tests" / "test_user.py"
    user.write_text(
        "from tests.test_base import ROOT\nfrom tests.test_fine import X\n"
        "from tests.test_missing import Y\nfrom tests.test_body_only import test_x\n"
    )
    assert missing_needs(user, root) == ["tests.test_base", "tests.test_missing"]
    grouped = root / "tests" / "test_grouped.py"
    grouped.write_text(
        "from tests import test_fine as fine, test_base as base\n"
        "import tests.test_missing\nfrom tests import test_body_only\n"
    )
    assert missing_needs(grouped, root) == ["tests.test_base", "tests.test_missing"]
    loop_a = root / "tests" / "test_loop_a.py"
    loop_a.write_text("from tests.test_loop_b import B\n")
    (root / "tests" / "test_loop_b.py").write_text("from tests import test_loop_a\n")
    assert missing_needs(loop_a, root) == []  # a cycle is not a missing need


def test_a_test_body_need_fails_the_required_gate(tmp_path):
    root = tree(tmp_path)
    (root / "tests" / "test_fine.py").write_text("X = 1\n")
    (root / "tests" / "test_base.py").write_text("from scripts.absent import b\n")
    skip_unless_shipped("scripts.present", "tests.test_fine", root=root)  # returns
    with pytest.raises(pytest.fail.Exception) as skipped:
        skip_unless_shipped(
            "scripts.present", "scripts.absent", "tests.test_base", "deliverables/", root=root
        )
    assert str(skipped.value) == (
        "not shipped in this tree: scripts.absent, tests.test_base, deliverables/"
    )
    (root / "deliverables").mkdir()
    (root / "scripts" / "absent.py").write_text("")
    skip_unless_shipped("scripts.absent", "tests.test_base", "deliverables/", root=root)
    with pytest.raises(ValueError):
        skip_unless_shipped("runtime.voice_core", root=root)


def test_summary_names_each_left_out_file_with_its_needs(monkeypatch):
    lines = []
    reporter = SimpleNamespace(write_line=lines.append)
    monkeypatch.setattr(gate, "LEFT_OUT", {})
    gate.pytest_terminal_summary(reporter)
    assert lines == []
    monkeypatch.setattr(
        gate,
        "LEFT_OUT",
        {"test_b.py": ["scripts.x", "deliverables/"], "test_a.py": ["tests.test_c"]},
    )
    gate.pytest_terminal_summary(reporter)
    assert lines == [
        "evidence not shipped: 2 test files left out, each with what this tree lacks:",
        "  test_a.py: tests.test_c",
        "  test_b.py: scripts.x, deliverables/",
    ]


def test_a_file_the_run_did_not_ask_for_is_not_named_as_left_out(tmp_path):
    config = SimpleNamespace(
        args=["tests/test_a.py::test_x"], invocation_params=SimpleNamespace(dir=tmp_path)
    )
    assert gate.requested(tmp_path / "tests" / "test_a.py", config)
    assert not gate.requested(tmp_path / "tests" / "test_b.py", config)
    config.args = ["tests"]
    assert gate.requested(tmp_path / "tests" / "test_b.py", config)


def test_the_gate_itself_needs_nothing():
    # Test files import helpers from it; a need of its own would leave them all out.
    assert missing_needs(ROOT / "tests" / "conftest.py") == []
