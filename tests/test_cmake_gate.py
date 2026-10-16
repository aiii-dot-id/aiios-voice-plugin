"""Broken CMake is a failed prerequisite, never a passing skipped contract."""

import os

import pytest

from tests.conftest import unusable_cmake

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the fake executables are POSIX shell scripts")


def executable(path, status):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\nexit {status}\n")
    path.chmod(0o755)
    return path


def test_no_cmake_anywhere_skips_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert unusable_cmake() is None
    assert unusable_cmake(tmp_path / "fallback" / "cmake") is None


def test_a_cmake_that_runs_skips_nothing(tmp_path, monkeypatch):
    executable(tmp_path / "bin" / "cmake", 0)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert unusable_cmake() is None
    fallback = executable(tmp_path / "fallback" / "cmake", 0)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert unusable_cmake(fallback) is None


def test_a_cmake_that_exists_but_does_not_run_is_named(tmp_path, monkeypatch):
    broken = executable(tmp_path / "bin" / "cmake", 1)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    with pytest.raises(pytest.fail.Exception, match="CMake prerequisite"):
        unusable_cmake()
    fallback = executable(tmp_path / "fallback" / "cmake", 1)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(pytest.fail.Exception, match="CMake prerequisite"):
        unusable_cmake(fallback)
    executable(tmp_path / "working" / "cmake", 0)
    monkeypatch.setenv("PATH", str(tmp_path / "working"))
    assert unusable_cmake(fallback) is None  # the cmake on PATH is the one a suite runs
