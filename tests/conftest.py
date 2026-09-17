"""Required tests fail on missing inputs; optional research browsing is explicit.

Historical audit tests require separately retained artifacts. Normal collection
does not hide absent inputs. Only explicit --allow-missing-evidence browsing
uses the dependency inventory below, and it cannot be combined with a required
--fail-on-skips gate. No test-pattern match or missing dependency is a pass.
"""

import ast
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ALLOW_MISSING_EVIDENCE = False


def pytest_addoption(parser):
    parser.addoption("--allow-missing-evidence", action="store_true",
                     help="Exploratory collection only: omit unavailable historical evidence; not a gate")
    parser.addoption("--fail-on-skips", action="store_true",
                     help="Fail validation when any selected test is skipped")


def pytest_configure(config):
    global ALLOW_MISSING_EVIDENCE
    ALLOW_MISSING_EVIDENCE = config.getoption("--allow-missing-evidence")
    if ALLOW_MISSING_EVIDENCE and config.getoption("--fail-on-skips"):
        raise pytest.UsageError("a required gate cannot allow missing evidence")


def pytest_sessionfinish(session, exitstatus):
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if session.config.getoption("--fail-on-skips") and reporter and reporter.stats.get("skipped"):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def unusable_cmake(fallback=None):
    """The cmake a suite would run, when it exists but does not run; else None.

    A broken launcher fails prerequisite admission instead of skipping proof.
    The cmake on PATH comes first, then the suite's own `fallback` path. When
    no cmake exists at all nothing is skipped, and those suites fail, as a
    native build contract without CMake should."""
    cmake = shutil.which("cmake")
    if cmake is None and fallback is not None and Path(fallback).is_file():
        cmake = str(fallback)
    if cmake is None:
        return None
    try:
        if subprocess.run([cmake, "--version"], capture_output=True, timeout=20).returncode == 0:
            return None
    except (OSError, subprocess.SubprocessError):
        pass
    pytest.fail("CMake prerequisite does not run: " + cmake, pytrace=False)


EVIDENCE_DIRECTORY = re.compile(r"\b(deliverables|artifacts)/")
LEFT_OUT = {}


def import_time_nodes(node):
    """The nodes that run when a module is imported, in source order.

    A function body runs only when it is called, so it is not followed; its
    decorators and argument defaults, and a class body, run at import."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            arguments = child.args
            for part in (
                *getattr(child, "decorator_list", ()),
                *arguments.defaults,
                *(d for d in arguments.kw_defaults if d is not None),
            ):
                yield part
                yield from import_time_nodes(part)
            continue
        yield child
        yield from import_time_nodes(child)


def imported_modules(node):
    """The `scripts` and `tests` modules one import statement names, in any form."""
    if isinstance(node, ast.Import):
        names = [alias.name for alias in node.names]
    elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
        if node.module in ("scripts", "tests"):
            names = [node.module + "." + alias.name for alias in node.names]
        else:
            names = [node.module]
    else:
        return []
    modules = []
    for name in names:
        package, _, rest = name.partition(".")
        module = rest.partition(".")[0]
        if package in ("scripts", "tests") and module:
            modules.append(package + "." + module)
    return modules


def lacks(need, root=ROOT, _seen=()):
    """Whether `root` lacks one need: `scripts.<module>`, `tests.<module>` (absent
    or itself with an unmet import-time need) or an evidence directory `<name>/`."""
    if need.endswith("/"):
        return not (root / need).is_dir()
    package, _, module = need.partition(".")
    if package == "scripts":
        return not (root / "scripts" / (module + ".py")).is_file()
    if package == "tests":
        sibling = root / "tests" / (module + ".py")
        return not sibling.is_file() or bool(missing_needs(sibling, root, (*_seen, module)))
    raise ValueError("unknown need: " + need)


def missing_needs(path, root=ROOT, _seen=()):
    """What the code a test file runs at import needs and `root` lacks.

    Script modules and sibling test modules come in source order, then evidence
    directories by name. A file that does not parse needs nothing here, so its
    own collection error is what the run reports."""
    path = Path(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    except (SyntaxError, ValueError):
        return []
    modules, evidence = [], set()
    for node in import_time_nodes(tree):
        modules.extend(imported_modules(node))
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            evidence.update(name + "/" for name in EVIDENCE_DIRECTORY.findall(node.value))
    needs = []
    for need in dict.fromkeys(modules):
        package, _, module = need.partition(".")
        if package == "tests" and (module in _seen or root / "tests" / (module + ".py") == path):
            continue  # a cycle, or a file naming itself, is not a missing need
        if lacks(need, root, _seen):
            needs.append(need)
    needs.extend(name for name in sorted(evidence) if lacks(name, root))
    return needs


def skip_unless_shipped(*needs, root=ROOT):
    """Fail on missing body-level inputs; only exploratory browsing may skip.

    The file stays collected, so its independently runnable siblings still run.
    The historical helper name is retained for existing artifact-audit callers.
    """
    missing = [need for need in needs if lacks(need, root)]
    if missing:
        reason = "not shipped in this tree: " + ", ".join(missing)
        if ALLOW_MISSING_EVIDENCE:
            pytest.skip(reason)
        pytest.fail(reason, pytrace=False)


def requested(path, config):
    """Whether this run asked for `path`; a file it never named is not left out of it."""
    for arg in config.args:
        target = (Path(config.invocation_params.dir) / arg.split("::")[0]).resolve()
        if path == target or target in path.parents:
            return True
    return False


def pytest_ignore_collect(collection_path, config):
    if not config.getoption("--allow-missing-evidence"):
        return None
    path = Path(collection_path)
    if path.parent != ROOT / "tests" or not path.name.startswith("test_") or path.suffix != ".py":
        return None
    if not requested(path, config):
        return None
    needs = missing_needs(path)
    if needs:
        LEFT_OUT[path.name] = needs
        return True
    return None


def pytest_terminal_summary(terminalreporter):
    if not LEFT_OUT:
        return
    terminalreporter.write_line(
        f"evidence not shipped: {len(LEFT_OUT)} test files left out, each with what this tree lacks:"
    )
    for name, needs in sorted(LEFT_OUT.items()):
        terminalreporter.write_line(f"  {name}: {', '.join(needs)}")
