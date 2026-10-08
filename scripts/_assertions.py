"""Refuse to run a release gate when Python has removed its checks.

Most checks in the gates and proofs of this directory are `assert` statements.
Python started with `-O` or `-OO`, or with PYTHONOPTIMIZE set to anything but
an empty value or 0, compiles every `assert` away, in every module of the
process, so a gate would end with exit code 0 having checked nothing.
`require_assertions` ends the process with one plain sentence instead.

The rule, enforced by tests/test_release_gates_refuse_optimised_python.py:

* A file here that contains an `assert`, and a program here that imports such
  a file (directly or through other modules), calls `require_assertions()` as
  its first executable statement, before any other import. A module's own
  asserts are already gone by the time it is compiled, so nothing may run
  ahead of the refusal.
* Optimisation is a property of the interpreter, not of a module. A gate's own
  call therefore answers for every library module it imports afterwards: a
  library full of asserts that is imported by a gate is covered by that gate's
  first statement.
* The files with asserts call it themselves all the same, because programs
  kept outside this tree import them as libraries and this tree cannot
  put a first statement into those programs.

This module has to stay importable under optimisation, so it contains no
`assert` and decides from `__debug__` and `sys.flags.optimize` alone. Without
optimisation the call does nothing: importing a gate as a library, as the
tests do, is unaffected, and pytest is not run under optimisation.
"""
import sys

REFUSAL = (
    "this is a release gate: its checks are assert statements and Python was "
    "started with optimisation, which removes them; run it without -O and "
    "without PYTHONOPTIMIZE"
)


def assertions_removed() -> bool:
    """Whether this interpreter compiles `assert` statements away."""
    return (not __debug__) or sys.flags.optimize != 0


def require_assertions() -> None:
    """End the process with REFUSAL unless `assert` statements are executed."""
    if assertions_removed():
        raise SystemExit(REFUSAL)
