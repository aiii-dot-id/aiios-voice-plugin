"""The evidence gate names exactly what a test file needs and the tree lacks."""

from tests.conftest import missing_needs


def test_gate_names_absent_needs_and_keeps_present_ones(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "present.py").write_text("")
    (tmp_path / "tests").mkdir()
    evidence = "deliverables"
    needy = tmp_path / "tests" / "test_needy.py"
    needy.write_text(
        "from scripts.present import a\nfrom scripts.absent import b\n"
        "from scripts import present as p, gone as g\n"
        "def test_x():\n    from scripts import later\n"
        "P = ROOT / '" + evidence + "/run-1/result.json'\n"
    )
    assert missing_needs(needy, tmp_path) == [
        "scripts.absent", "scripts.gone", "scripts.later", evidence + "/",
    ]
    satisfied = tmp_path / "tests" / "test_satisfied.py"
    satisfied.write_text("from scripts.present import a\nfrom scripts import present\n")
    assert missing_needs(satisfied, tmp_path) == []
    (tmp_path / evidence).mkdir()
    assert missing_needs(needy, tmp_path) == ["scripts.absent", "scripts.gone", "scripts.later"]


def test_gate_follows_sibling_test_modules(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_base.py").write_text("from scripts.absent import b\n")
    (tmp_path / "tests" / "test_fine.py").write_text("X = 1\n")
    user = tmp_path / "tests" / "test_user.py"
    user.write_text(
        "from tests.test_base import ROOT\nfrom tests.test_fine import X\n"
        "from tests.test_missing import Y\n"
    )
    assert missing_needs(user, tmp_path) == ["tests.test_base", "tests.test_missing"]
    loop_a = tmp_path / "tests" / "test_loop_a.py"
    loop_a.write_text("from tests.test_loop_b import B\n")
    (tmp_path / "tests" / "test_loop_b.py").write_text("from tests.test_loop_a import A\n")
    assert missing_needs(loop_a, tmp_path) == []  # a cycle is not a missing need
