"""A release gate must not pass because Python removed its checks.

`python -O`, `-OO` and PYTHONOPTIMIZE compile every `assert` away. Most checks
of the gates under scripts/ are asserts, so each such gate refuses that Python
through scripts/_assertions.py before it does anything else.

The rule this file enforces, for every Python file under scripts/:

1. a file that contains an `assert` statement calls the guard first, whether it
   is run as a program or imported as a library (release chains kept outside
   this tree import the libraries directly);
2. a program that contains none but imports, directly or through other modules
   of this repository, a file that does, calls the guard first as well.

A program is a file with a top-level `if __name__ == "__main__":` block, or
with anything at module level other than a docstring, an import, a definition
or an assignment (a script that parses its arguments at import, for one).

"Calls the guard first" means: after the module docstring and any
`from __future__` import, the next two statements are exactly

    from scripts._assertions import require_assertions
    require_assertions()

A program outside scripts/ and tests/ that holds asserts is run by hand from
its own directory, where it cannot import that module. It is named in BY_HAND
and carries the same refusal inline, as its first executable statements:

    import sys
    if not __debug__ or sys.flags.optimize:
        raise SystemExit("<the sentence of scripts/_assertions.py>")

Any other such program fails this file until it is named and refuses.

Statements are read with `ast`, never matched as text. No model, device or
compiled artifact is used. This file is itself listed in the CI workflow and
in the source closeout, and says so if it is dropped from either.
"""
import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts._assertions import REFUSAL

ROOT = Path(__file__).resolve().parents[1]
GUARD_MODULE, GUARD = "scripts._assertions", "require_assertions"
# Programs outside scripts/ and tests/ that hold asserts and are run by hand.
BY_HAND = ("runtime/native_uid_ecapa/generate_tables.py",)
QUIET = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef,
         ast.ClassDef, ast.Assign, ast.AnnAssign)


def is_docstring(node):
    return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str))


def is_guard_call(node):
    return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name) and node.value.func.id == GUARD
            and not node.value.args and not node.value.keywords)


def is_main_block(node):
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    sides = [node.test.left, *node.test.comparators]
    return (any(isinstance(s, ast.Name) and s.id == "__name__" for s in sides)
            and any(isinstance(s, ast.Constant) and s.value == "__main__" for s in sides))


def first_statements(tree):
    """The module body after its docstring and any `from __future__` import."""
    body = list(tree.body)
    if body and is_docstring(body[0]):
        body = body[1:]
    while body and isinstance(body[0], ast.ImportFrom) and body[0].module == "__future__":
        body = body[1:]
    return body


def calls_guard_first(tree):
    body = first_statements(tree)
    if len(body) < 2:
        return False
    load, call = body[:2]
    return (isinstance(load, ast.ImportFrom) and load.level == 0 and load.module == GUARD_MODULE
            and [(a.name, a.asname) for a in load.names] == [(GUARD, None)]
            and is_guard_call(call))


def refuses_inline(tree):
    """`import sys`, then the guard's own test raising the guard's own sentence."""
    body = first_statements(tree)
    if len(body) < 2:
        return False
    load, check = body[:2]
    wanted = ast.parse(f"if not __debug__ or sys.flags.optimize:\n    raise SystemExit({REFUSAL!r})\n").body[0]
    return (isinstance(load, ast.Import) and [(a.name, a.asname) for a in load.names] == [("sys", None)]
            and ast.dump(check) == ast.dump(wanted))


def is_program(tree):
    for index, node in enumerate(tree.body):
        if is_main_block(node):
            return True
        if (index == 0 and is_docstring(node)) or is_guard_call(node):
            continue
        if not isinstance(node, QUIET):
            return True
    return False


class Tree:
    """The Python files of one source tree, parsed once, with their local imports."""

    def __init__(self, root):
        self.root = Path(root)
        self.parsed, self.imported, self.counted = {}, {}, {}

    def parse(self, path):
        if path not in self.parsed:
            self.parsed[path] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        return self.parsed[path]

    def module_files(self, dotted):
        """The files of this tree that importing `dotted` executes."""
        found, parts = [], dotted.split(".")
        for end in range(1, len(parts) + 1):
            base = self.root.joinpath(*parts[:end])
            for candidate in (base.with_suffix(".py"), base / "__init__.py"):
                if candidate.is_file():
                    found.append(candidate)
        return found

    def imports(self, path):
        if path in self.imported:
            return self.imported[path]
        package = list(path.relative_to(self.root).with_suffix("").parts[:-1])
        files = self.imported[path] = set()
        for node in ast.walk(self.parse(path)):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = package[:len(package) - node.level + 1] if node.level else []
                module = ".".join(base + ([node.module] if node.module else []))
                names = [module] + [module + "." + alias.name for alias in node.names]
            else:
                continue
            for name in names:
                files.update(self.module_files(name.strip(".")))
        files.discard(path)
        return files

    def asserts(self, path):
        if path not in self.counted:
            self.counted[path] = sum(isinstance(node, ast.Assert) for node in ast.walk(self.parse(path)))
        return self.counted[path]

    def imported_asserts(self, path):
        """Files with asserts that running or importing `path` can bring in."""
        seen, pending, bearing = {path}, [path], []
        while pending:
            for other in sorted(self.imports(pending.pop())):
                if other not in seen:
                    seen.add(other)
                    pending.append(other)
                    if self.asserts(other):
                        bearing.append(other)
        return sorted(bearing)

    def must_refuse(self, path):
        """Why this file has to call the guard first, or None when it need not."""
        tree = self.parse(path)
        count = self.asserts(path)
        if count:
            return f"contains {count} assert statement(s)"
        if is_program(tree):
            bearing = self.imported_asserts(path)
            if bearing:
                names = ", ".join(p.relative_to(self.root).as_posix() for p in bearing[:3])
                return f"is a program that imports files with assert statements ({names})"
        return None

    def omissions(self):
        """One line for each file under scripts/ that breaks the rule."""
        lines = []
        for path in sorted((self.root / "scripts").rglob("*.py")):
            reason = self.must_refuse(path)
            if reason and not calls_guard_first(self.parse(path)):
                lines.append(f"{path.relative_to(self.root).as_posix()}: {reason} and does not call "
                             f"{GUARD}() from {GUARD_MODULE} as its first executable statement")
        return lines


def test_every_gate_with_asserts_calls_the_guard_first():
    tree = Tree(ROOT)
    omissions = tree.omissions()
    assert not omissions, "release gates that optimised Python would empty:\n" + "\n".join(omissions)
    # The walk must have found the gates: an empty walk would also report nothing.
    held = [p for p in sorted((ROOT / "scripts").rglob("*.py")) if tree.must_refuse(p)]
    assert len(held) >= 10 and all(calls_guard_first(tree.parse(p)) for p in held)


def test_the_guard_module_does_not_depend_on_asserts():
    source = ast.parse((ROOT / "scripts/_assertions.py").read_text(encoding="utf-8"))
    assert not any(isinstance(node, ast.Assert) for node in ast.walk(source))
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) and
                   [alias.name for alias in node.names] != ["sys"] for node in ast.walk(source))


GUARDED = f"from {GUARD_MODULE} import {GUARD}\n{GUARD}()\n"
CHECKS = "def check(value):\n    assert value == 'sound', 'the release is not sound'\n"
RUNS = "if __name__ == '__main__':\n    main()\n"


def synthetic(tmp_path, files):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "__init__.py").write_text("")
    shutil.copyfile(ROOT / "scripts/_assertions.py", scripts / "_assertions.py")
    for name, text in files.items():
        (scripts / name).write_text(text)
    return Tree(tmp_path)


@pytest.mark.parametrize("name, files, named", [
    ("a library with asserts", {"checks.py": '"""Checks."""\n' + CHECKS}, ["scripts/checks.py"]),
    ("a program with asserts", {"gate.py": "import sys\n" + CHECKS + "def main(): check(sys.argv[1])\n" + RUNS},
     ["scripts/gate.py"]),
    ("a program that only imports them",
     {"checks.py": GUARDED + CHECKS,
      "gate.py": "import sys\nfrom scripts.checks import check\ndef main(): check(sys.argv[1])\n" + RUNS},
     ["scripts/gate.py"]),
    ("a program that imports them inside a function, through another module",
     {"checks.py": GUARDED + CHECKS, "between.py": "from . import checks\n",
      "gate.py": "def main():\n    from scripts import between\n" + RUNS},
     ["scripts/gate.py"]),
    ("a program without a main block",
     {"checks.py": GUARDED + CHECKS,
      "gate.py": "import sys\nfrom scripts.checks import check\ncheck(sys.argv[1])\n"},
     ["scripts/gate.py"]),
    ("a guard that is not first",
     {"gate.py": '"""Gate."""\nimport sys\n' + GUARDED + CHECKS + "check(sys.argv[1])\n"}, ["scripts/gate.py"]),
    ("a guard that is imported and never called",
     {"gate.py": f"from {GUARD_MODULE} import {GUARD}\nimport sys\n" + CHECKS}, ["scripts/gate.py"]),
    ("a guard inside the main block only",
     {"gate.py": "import sys\n" + CHECKS + "if __name__ == '__main__':\n    " + GUARDED.replace("\n", "\n    ")
      + "check(sys.argv[1])\n"}, ["scripts/gate.py"]),
    ("guarded gates, and a library that only imports asserts",
     {"checks.py": '"""Checks."""\nfrom __future__ import annotations\n' + GUARDED + CHECKS,
      "names.py": "from scripts.checks import check\nNAMES = ('sound',)\n",
      "gate.py": '"""Gate."""\n' + GUARDED + "import sys\nfrom scripts.names import check\n"
                 "def main(): check(sys.argv[1])\n" + RUNS},
     []),
])
def test_the_rule_names_each_kind_of_omission(tmp_path, name, files, named):
    lines = synthetic(tmp_path, files).omissions()
    assert [line.split(":")[0] for line in lines] == named, (name, lines)


def run(arguments, *, cwd=ROOT, optimise=None, modules=None):
    """Run this Python on `arguments`; `optimise` is a flag, 'env', or None for plain.

    `modules` is a directory the child imports from ahead of its installed packages."""
    # Options and plugins meant for the suite running this file are not the child's.
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONOPTIMIZE", "PYTEST_ADDOPTS")}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    if modules is not None:
        env["PYTHONPATH"] = str(modules)
    flags = []
    if optimise == "env":
        env["PYTHONOPTIMIZE"] = "1"
    elif optimise:
        flags = [optimise]
    return subprocess.run([sys.executable, *flags, *arguments], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=60)


# Cheap real gates, one of each kind the rule names: asserts of its own; none of
# its own but imported ones; a program without a main block; a library.
OWN, IMPORTED, NO_MAIN, LIBRARY = ("audit_native_enrollment_desktops", "verify_publication_catalog",
                                   "prove_native_uid_refinement", "native_loaded_images")


def refused(result):
    """The process ended on the guard's sentence: no traceback, nothing printed, exit code 1."""
    return (result.returncode == 1 and not result.stdout and "Traceback" not in result.stderr
            and result.stderr.strip().splitlines()[-1:] == [REFUSAL])


def test_the_examples_are_still_what_they_stand_for():
    tree = Tree(ROOT)
    path = {name: ROOT / "scripts" / (name + ".py") for name in (OWN, IMPORTED, NO_MAIN, LIBRARY)}
    assert tree.asserts(path[OWN]) and is_program(tree.parse(path[OWN]))
    assert not tree.asserts(path[IMPORTED]) and tree.imported_asserts(path[IMPORTED])
    assert is_program(tree.parse(path[NO_MAIN])) and not any(map(is_main_block, tree.parse(path[NO_MAIN]).body))
    assert tree.asserts(path[LIBRARY]) and not is_program(tree.parse(path[LIBRARY]))


@pytest.mark.parametrize("optimise", ["-O", "-OO", "env"])
@pytest.mark.parametrize("arguments", [
    ["-m", "scripts." + OWN, "--help"], ["-m", "scripts." + IMPORTED, "--help"],
    ["-m", "scripts." + NO_MAIN, "--help"], ["-c", "import scripts." + LIBRARY],
])
def test_real_gates_refuse_optimised_python(arguments, optimise):
    result = run(arguments, optimise=optimise)
    assert refused(result), (result.returncode, result.stdout, result.stderr)


@pytest.mark.parametrize("arguments, shown", [
    (["-m", "scripts." + OWN, "--help"], "usage:"), (["-m", "scripts." + IMPORTED, "--help"], "usage:"),
    (["-m", "scripts." + NO_MAIN, "--help"], "usage:"), (["-c", "import scripts." + LIBRARY], ""),
])
def test_real_gates_start_without_optimisation(arguments, shown):
    result = run(arguments)
    assert result.returncode == 0 and shown in result.stdout and REFUSAL not in result.stderr, (
        result.returncode, result.stdout, result.stderr)


def test_a_failing_check_cannot_pass_under_optimisation(tmp_path):
    gate = '"""A gate whose one check is an assert."""\n{guard}import sys\n' + CHECKS + (
        "check(sys.argv[1])\nprint('gate passed')\n")
    synthetic(tmp_path, {"unguarded.py": gate.format(guard=""), "guarded.py": gate.format(guard=GUARDED)})
    (tmp_path / "scripts/checks.py").write_text(CHECKS)
    (tmp_path / "test_release.py").write_text(
        "from scripts.checks import check\ndef test_release(): check('broken')\n")

    # The defect: with its check removed, a gate over a broken release passes.
    hole = run(["-m", "scripts.unguarded", "broken"], cwd=tmp_path, optimise="-O")
    assert hole.returncode == 0 and "gate passed" in hole.stdout, (hole.stdout, hole.stderr)
    # The check is live without optimisation, and a sound release passes.
    caught = run(["-m", "scripts.guarded", "broken"], cwd=tmp_path)
    assert caught.returncode == 1 and "the release is not sound" in caught.stderr and not caught.stdout
    sound = run(["-m", "scripts.guarded", "sound"], cwd=tmp_path)
    assert sound.returncode == 0 and sound.stdout.strip() == "gate passed", (sound.stdout, sound.stderr)
    # The guarded gate refuses instead of passing, however optimisation was asked for.
    for optimise in ("-O", "-OO", "env"):
        result = run(["-m", "scripts.guarded", "broken"], cwd=tmp_path, optimise=optimise)
        assert refused(result), (optimise, result.stdout, result.stderr)

    # The same through pytest, which keeps the asserts of test modules but not
    # those of the modules a test imports: the test over a broken release
    # passes until the imported module refuses.
    pytest_run = ["-m", "pytest", "-q", "-p", "no:cacheprovider", "test_release.py"]
    assert run(pytest_run, cwd=tmp_path).returncode == 1
    hole = run(pytest_run, cwd=tmp_path, optimise="-O")
    assert hole.returncode == 0 and "1 passed" in hole.stdout, (hole.stdout, hole.stderr)
    (tmp_path / "scripts/checks.py").write_text(GUARDED + CHECKS)
    result = run(pytest_run, cwd=tmp_path, optimise="-O")
    assert result.returncode != 0 and REFUSAL in result.stdout + result.stderr, (result.stdout, result.stderr)


def outside_programs_with_asserts(tree):
    """Programs with asserts of their own outside scripts/ and tests/, as repository paths."""
    found = []
    for top in sorted(tree.root.iterdir()):
        if top.is_dir() and not top.name.startswith(".") and top.name not in ("scripts", "tests"):
            found += [path.relative_to(tree.root).as_posix() for path in sorted(top.rglob("*.py"))
                      if tree.asserts(path) and is_program(tree.parse(path))]
    return found


def test_programs_run_by_hand_are_named_and_refuse_inline():
    tree = Tree(ROOT)
    assert outside_programs_with_asserts(tree) == sorted(BY_HAND), (
        "a program outside scripts/ and tests/ holds asserts: name it in BY_HAND and give it the inline refusal")
    for name in BY_HAND:
        assert refuses_inline(tree.parse(ROOT / name)), name + " does not refuse optimised Python first"


@pytest.mark.parametrize("source, inline", [
    ("import sys\nif not __debug__ or sys.flags.optimize:\n    raise SystemExit({0!r})\nassert sys.argv\n", True),
    ('"""Doc."""\nimport sys\nif not __debug__ or sys.flags.optimize:  # why\n    raise SystemExit(\n'
     "        {0!r})\nimport json\n", True),
    ("import sys\nif sys.flags.optimize:\n    raise SystemExit({0!r})\n", False),
    ("import sys\nif not __debug__ or sys.flags.optimize:\n    raise SystemExit('another sentence')\n", False),
    ("import sys\nif not __debug__ or sys.flags.optimize:\n    print({0!r})\n", False),
    ("import sys, json\nif not __debug__ or sys.flags.optimize:\n    raise SystemExit({0!r})\n", False),
    ("import json\nimport sys\nif not __debug__ or sys.flags.optimize:\n    raise SystemExit({0!r})\n", False),
])
def test_the_inline_refusal_is_read_exactly(source, inline):
    assert refuses_inline(ast.parse(source.format(REFUSAL))) is inline


@pytest.mark.parametrize("optimise", ["-O", "-OO", "env"])
@pytest.mark.parametrize("name", BY_HAND)
def test_programs_run_by_hand_refuse_optimised_python(tmp_path, name, optimise):
    # By path and from its own directory, as its README runs it; it must refuse
    # before it imports anything, so nothing it needs has to be installed.
    program = ROOT / name
    result = run([program.name, str(tmp_path / "never-written")], cwd=program.parent, optimise=optimise)
    assert refused(result) and not list(tmp_path.iterdir()), (result.returncode, result.stdout, result.stderr)


def test_the_table_generator_starts_without_optimisation(tmp_path):
    # Empty stand-ins for the two packages it imports: only its own first
    # statements and its argument parser run, and nothing is computed.
    for package in ("speechbrain", "speechbrain/lobes", "speechbrain/processing"):
        (tmp_path / package).mkdir()
        (tmp_path / package / "__init__.py").write_text("")
    (tmp_path / "speechbrain/lobes/features.py").write_text("Fbank = None\n")
    (tmp_path / "speechbrain/processing/features.py").write_text("InputNormalization = None\n")
    (tmp_path / "torch.py").write_text("")
    program = ROOT / "runtime/native_uid_ecapa/generate_tables.py"
    result = run([program.name, "--help"], cwd=program.parent, modules=tmp_path)
    assert result.returncode == 0 and "usage:" in result.stdout and REFUSAL not in result.stderr, (
        result.returncode, result.stdout, result.stderr)


def test_this_file_is_run_by_the_ci_and_by_the_source_closeout():
    from scripts.validate_source_closeout import TESTS
    name = Path(__file__).name
    workflow = (ROOT / ".github/workflows/source-contracts.yml").read_text(encoding="utf-8")
    assert "tests/" + name in workflow.split(), "the CI no longer runs " + name
    assert Path(name).stem in TESTS, "the source closeout no longer runs " + name
