"""Evidence this tree does not ship is left out by name, never reported as breakage.

Most of this suite audits retained build and evidence directories and imports
helper modules that live beside them. A test file that needs a `scripts.<module>`
absent from scripts/, a sibling `tests.<module>` that is absent or itself left
out, or reads under an evidence directory absent from the root, is not
collected, and the run's summary says how many files were left out and why. A
tree that carries the evidence collects everything; this gate changes nothing
there (review, 2026-09-16: 82 of 129 files errored at import here).
"""

import functools
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@functools.lru_cache(maxsize=None)
def cmake_usable():
    """Whether the cmake on PATH runs at all; a broken launcher is not a tool.

    The suites that configure real projects skip on this, naming it, instead
    of reporting a launcher's traceback as a failed link contract."""
    cmake = shutil.which("cmake")
    if not cmake:
        return False
    try:
        return subprocess.run([cmake, "--version"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
# Statements at any indentation: a module imported inside a test body is still
# a need of the file.
SCRIPT_MODULE = re.compile(r"^\s*(?:from|import)\s+scripts\.([A-Za-z_][A-Za-z0-9_]*)", re.M)
SCRIPT_NAMES = re.compile(r"^\s*from\s+scripts\s+import\s+([^\n#(]+)", re.M)
SIBLING_MODULE = re.compile(r"^\s*(?:from|import)\s+tests\.([A-Za-z_][A-Za-z0-9_]*)", re.M)
EVIDENCE_DIRECTORY = re.compile(r"\b(deliverables|artifacts)/")
LEFT_OUT = {}


def script_modules(text):
    """Every scripts module a source text imports, in either import form."""
    names = list(SCRIPT_MODULE.findall(text))
    for group in SCRIPT_NAMES.findall(text):
        for item in group.split(","):
            name = item.strip().split(" as ")[0].strip()
            if name:
                names.append(name)
    return names


def missing_needs(path, root=ROOT, _seen=()):
    """The script modules, sibling test modules and evidence directories a test
    file needs and `root` lacks; a sibling with needs of its own counts as one."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    needs = []
    for module in script_modules(text):
        if not (root / "scripts" / (module + ".py")).is_file():
            needs.append("scripts." + module)
    for module in SIBLING_MODULE.findall(text):
        sibling = root / "tests" / (module + ".py")
        if module in _seen or sibling == path:
            continue
        if not sibling.is_file():
            needs.append("tests." + module)
        elif missing_needs(sibling, root, (*_seen, module)):
            needs.append("tests." + module)
    for name in sorted(set(EVIDENCE_DIRECTORY.findall(text))):
        if not (root / name).is_dir():
            needs.append(name + "/")
    return needs


def pytest_ignore_collect(collection_path, config):
    path = Path(collection_path)
    if path.parent != ROOT / "tests" or not path.name.startswith("test_") or path.suffix != ".py":
        return None
    needs = missing_needs(path)
    if needs:
        LEFT_OUT[path.name] = needs
        return True
    return None


def pytest_terminal_summary(terminalreporter):
    if LEFT_OUT:
        terminalreporter.write_line(
            f"evidence not shipped: {len(LEFT_OUT)} test files left out; each names a "
            "scripts module, a sibling test module or an evidence directory absent from this tree"
        )
