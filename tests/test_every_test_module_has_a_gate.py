import re
from pathlib import Path

from scripts.validate_source_closeout import OUTSIDE_THE_GATES, PYTHON_DOUBLE, TESTS

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/source-contracts.yml"

PYTHON_ENGINE_MARKS = (
    "runtime.plugin_engine", "runtime.windows_voice", "runtime.cuda_voice", "runtime.voice_core",
    "runtime.voice_app", "runtime.stt", "runtime.speech_output", "runtime.speaker_identity",
    "runtime.native_endpoint", "runtime.native_pocket", "runtime.model_assets", "runtime.onnx_runtime",
    "plugin_worker_fixture", "fixture=True",
)


def modules():
    found = {path.stem for path in (ROOT / "tests").glob("test_*.py")}
    nested = sorted(str(path.relative_to(ROOT)) for path in (ROOT / "tests").rglob("test_*.py") if path.parent != ROOT / "tests")
    assert not nested, f"test modules below tests/ are collected by no list: {nested}"
    return found


def workflow_commands(text):
    lines, commands, at = text.splitlines(), [], 0
    while at < len(lines):
        key = re.match(r"^(\s*)(?:-\s+)?run:\s*(.*)$", lines[at])
        at += 1
        if not key:
            continue
        indent, value = len(key.group(1)), key.group(2).strip()
        if value[:1] in (">", "|"):
            block = []
            while at < len(lines) and (not lines[at].strip() or len(lines[at]) - len(lines[at].lstrip()) > indent):
                block.append(lines[at].strip())
                at += 1
            value = " ".join(block)
        commands.append(value)
    return commands


def workflow_modules():
    named = set()
    for command in workflow_commands(WORKFLOW.read_text(encoding="utf-8")):
        named |= set(re.findall(r"(?<![\w/.-])tests/(test_\w+)\.py(?![\w.])", command))
    return named


def test_the_workflow_is_read_and_runs_this_test():
    commands = workflow_commands(WORKFLOW.read_text(encoding="utf-8"))
    assert len(commands) > 10, "the workflow's run commands were not found; its layout or this reader changed"
    named = workflow_modules()
    assert Path(__file__).stem in named, "the workflow does not run the test that holds its list to tests/"
    assert workflow_commands("steps:\n  - name: tests/test_a.py\n    # run: python tests/test_b.py\n"
                             "    run: >-\n      python -m pytest\n      tests/test_c.py\n  - run: python tests/test_d.py\n") == [
        "python -m pytest tests/test_c.py", "python tests/test_d.py"]


def test_every_test_module_is_run_by_a_gate_or_says_why_not():
    assert len(set(TESTS)) == len(TESTS), "a module is named twice in the closeout's TESTS"
    gated, outside = workflow_modules() | set(TESTS), set(OUTSIDE_THE_GATES)
    in_no_list = sorted(modules() - gated - outside)
    assert not in_no_list, (
        f"no gate runs these test modules and nothing says why: {in_no_list}. Name each in the workflow "
        "(it needs only Python and requirements-test.txt), in TESTS of scripts/validate_source_closeout.py "
        "(it needs the fixture worker or the carrier build), or in OUTSIDE_THE_GATES there with its reason")
    in_both = sorted(gated & outside)
    assert not in_both, f"these modules are run by a gate and also said to be outside the gates: {in_both}"


def test_nothing_a_list_names_is_missing_from_tests():
    present = modules()
    for where, named in (("the workflow", workflow_modules()), ("the closeout's TESTS", set(TESTS)),
                         ("OUTSIDE_THE_GATES", set(OUTSIDE_THE_GATES)), ("PYTHON_DOUBLE", set(PYTHON_DOUBLE))):
        gone = sorted(named - present)
        assert not gone, f"{where} names test modules that are not in tests/: {gone}"


def test_every_module_outside_the_gates_says_why_and_where():
    for name, what in PYTHON_DOUBLE.items():
        assert isinstance(what, str) and what.strip(), f"PYTHON_DOUBLE does not say what {name} exercises"
    for name, why in OUTSIDE_THE_GATES.items():
        assert isinstance(why, str) and len(why.split()) >= 12 and "run" in why, (
            f"{name} does not say why no gate runs it and where it does run: {why!r}")


def test_the_modules_of_the_python_double_are_named():
    touching = set()
    for path in (ROOT / "tests").glob("test_*.py"):
        if path != Path(__file__).resolve() and any(mark in path.read_text(encoding="utf-8") for mark in PYTHON_ENGINE_MARKS):
            touching.add(path.stem)
    unnamed = sorted(touching - set(PYTHON_DOUBLE))
    assert not unnamed, (
        f"these test modules name the Python engine's code and are not in PYTHON_DOUBLE of "
        f"scripts/validate_source_closeout.py: {unnamed}")
    stale = sorted(set(PYTHON_DOUBLE) - touching)
    assert not stale, f"PYTHON_DOUBLE names test modules whose text names nothing of the Python engine: {stale}"
