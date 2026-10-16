"""Shared source-sample-clock timing laws for AII Voice traces."""

from __future__ import annotations


def sample_boundary_monotonic_ns(
    *, origin_monotonic_ns: int, end_sample: int, sample_rate_hz: int
) -> int:
    """Return the earliest monotonic time at which an exclusive sample end exists."""
    if origin_monotonic_ns <= 0:
        raise ValueError("origin_monotonic_ns must be positive")
    if end_sample <= 0:
        raise ValueError("end_sample must be positive")
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive")
    return origin_monotonic_ns + round(end_sample / sample_rate_hz * 1_000_000_000)


def sample_schedule_lag_ms(
    *,
    observed_monotonic_ns: int,
    origin_monotonic_ns: int,
    end_sample: int,
    sample_rate_hz: int,
) -> float:
    """Measure observation lag from the final source-sample arrival boundary."""
    due = sample_boundary_monotonic_ns(
        origin_monotonic_ns=origin_monotonic_ns,
        end_sample=end_sample,
        sample_rate_hz=sample_rate_hz,
    )
    return (observed_monotonic_ns - due) / 1_000_000
