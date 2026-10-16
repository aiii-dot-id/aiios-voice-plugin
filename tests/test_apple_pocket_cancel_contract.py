"""Source guard for the private Swift bridge's cancelled-start reset contract.

This is not physical iPhone or Core ML qualification. The ABI path must still
be exercised on the attached device before release promotion.
"""

from pathlib import Path
import shutil
import subprocess

import pytest


SOURCE = Path(__file__).resolve().parents[1] / (
    "runtime/portability/apple_native_adapter/Sources/ApplePocketBridge.swift"
)


def test_cancelled_start_records_generation_for_reset():
    text = SOURCE.read_text()
    worker_start = text.split("func start(_ id: UInt64", 1)[1].split("func next(", 1)[0]
    assert worker_start.index("generation = id") < worker_start.index(
        "guard id > cancelledThrough"
    )

    native_start = text.split('@_cdecl("nv_start")', 1)[1].split(
        '@_cdecl("nv_next")', 1
    )[0]
    assert "guard id > f.cancelled else" not in native_start
    assert "f.generation = id; f.computing = true; f.active = true" in native_start
    assert "try await h.worker.start(id," in native_start


def test_swift_bridge_parses():
    swiftc = shutil.which("swiftc")
    if swiftc is None:
        pytest.skip("Swift toolchain unavailable on this platform")
    subprocess.run([swiftc, "-frontend", "-parse", str(SOURCE)], check=True)
