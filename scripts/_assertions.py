import sys

REFUSAL = (
    "this is a release gate: its checks are assert statements and Python was "
    "started with optimisation, which removes them; run it without -O and "
    "without PYTHONOPTIMIZE"
)


def assertions_removed() -> bool:
    return (not __debug__) or sys.flags.optimize != 0


def require_assertions() -> None:
    if assertions_removed():
        raise SystemExit(REFUSAL)
