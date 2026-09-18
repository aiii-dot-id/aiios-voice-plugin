"""Required integration gate, not hardware, installed-product or release proof.

The historical audit tests outside this named scope keep their external inputs.
Missing selected tests, skips, empty collection and stale carriers fail this gate.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from scripts.build_plugin_carrier import ROOT, verify_build

TESTS = (
    "test_plugin_engine", "test_plugin_worker_cleanup_exit", "test_plugin_input_completion",
    "test_stt_evidence_client_close", "test_voice_core_trace_order",
    "test_voice_core_gapless_per_stream", "test_voice_core_playback_after_cancel",
    "test_release_notice_pins", "test_sdk_host_construction", "test_evidence_gate", "test_cmake_gate",
    "test_plugin_playback_wire", "test_plugin_process", "test_qualified_runtime_stage",
    "test_release_status_scope", "test_native_asr_link_contract", "test_native_build_profile",
    "test_native_windows_exports", "test_closeout_contracts", "test_plugin_carrier_build",
    "test_plugin_capture_processing", "test_plugin_readiness", "test_plugin_playback_receipts",
    "test_plugin_playback_control", "test_plugin_abort_drain", "test_plugin_startup_timing",
    "test_plugin_readonly_loading", "test_native_current_interrupt", "test_signed_windows_rebind",
    "test_native_capture_duration", "test_native_settings_packaging", "test_beta3_release_contract",
    "test_native_drain_progress",
    "test_native_lifetime",
    "test_plugin_sdk_declaration",
    "test_native_output_only",
)


def validate_environment(version=None, find_spec=importlib.util.find_spec):
    version = sys.version_info if version is None else version
    if version < (3, 11):
        raise ValueError("source gate requires Python 3.11+; see requirements-test.txt")
    missing = [name for name in ("pytest", "pytest_asyncio", "numpy") if find_spec(name) is None]
    if missing:
        raise ValueError("source gate dependencies missing: " + ", ".join(missing)
                         + "; install requirements-test.txt in an isolated environment")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--carrier-build", type=Path, required=True)
    parser.add_argument("--native-worker", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    validate_environment()
    build = args.carrier_build.resolve(strict=True)
    worker = args.native_worker.resolve(strict=True)
    verify_build(build)
    if not worker.is_file() or not os.access(worker, os.X_OK):
        raise ValueError("compiled native fixture worker required")
    tests = [ROOT / "tests" / (name + ".py") for name in TESTS]
    if len(set(tests)) != len(tests) or not all(p.is_file() for p in tests):
        raise ValueError("named test missing or duplicated")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, "-m", "pytest", "-q", "--fail-on-skips",
               "--junitxml=" + str(out / "pytest.xml"), *map(str, tests)]
    env = {**os.environ, "AII_TEST_CARRIER_BUILD": str(build),
           "AII_NATIVE_INTERRUPT_FIXTURE": str(worker)}
    with (out / "pytest.log").open("xb") as log:
        run = subprocess.run(command, cwd=ROOT, env=env, stdout=log,
                             stderr=subprocess.STDOUT, timeout=300)
    counts = dict(tests=0, failures=0, errors=0, skipped=0)
    if (out / "pytest.xml").is_file():
        for suite in ET.parse(out / "pytest.xml").getroot().iter("testsuite"):
            for key in counts:
                counts[key] += int(suite.get(key, "0"))
    passed = (run.returncode == 0 and counts["tests"] >= 239
              and not any(counts[k] for k in ("failures", "errors", "skipped")))
    result = dict(passed=passed, scope=__doc__, exit_code=run.returncode,
                  counts=counts, test_files=list(TESTS), command=command,
                  release_qualified=False)
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
