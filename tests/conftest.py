"""A required gate fails on a skipped test; a broken CMake fails instead of skipping."""

import shutil
import subprocess
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption("--fail-on-skips", action="store_true",
                     help="Fail validation when any selected test is skipped")


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
