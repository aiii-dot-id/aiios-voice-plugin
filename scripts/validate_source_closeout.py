"""Required integration gate, not hardware, installed-product or release proof.

Missing selected tests, skips, empty collection and stale carriers fail this gate.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from scripts.build_plugin_carrier import ROOT, verify_build

# EVERY TEST MODULE HAS A GATE, OR SAYS HERE WHY IT HAS NONE. Of 119 test modules, 32
# were once run by neither this gate nor the CI workflow. A test nothing runs proves nothing, and nobody is told when it stops
# passing. Each module under tests/ is now in one of three places, and
# tests/test_every_test_module_has_a_gate.py holds the three to the directory:
#
#   TESTS, below: this gate. What needs the compiled fixture worker or the carrier build.
#   .github/workflows/source-contracts.yml: what needs only Python, what
#     requirements-test.txt installs, and the compilers that workflow's runner builds with.
#   OUTSIDE_THE_GATES, below: what neither can run, each with its reason.
#
# A module may be in both gates. A module in neither is in OUTSIDE_THE_GATES.
TESTS = (
    "test_plugin_engine", "test_plugin_worker_cleanup_exit", "test_plugin_input_completion",
    "test_stt_evidence_client_close", "test_voice_core_trace_order",
    "test_voice_core_gapless_per_stream", "test_voice_core_playback_after_cancel",
    "test_release_notice_pins", "test_sdk_host_construction", "test_cmake_gate",
    "test_plugin_playback_wire", "test_plugin_process", "test_qualified_runtime_stage",
    "test_release_status_scope", "test_native_asr_link_contract", "test_native_build_profile",
    "test_native_windows_exports", "test_closeout_contracts", "test_plugin_carrier_build",
    "test_plugin_capture_processing", "test_plugin_readiness", "test_plugin_playback_receipts",
    "test_plugin_playback_control", "test_plugin_abort_drain", "test_plugin_startup_timing",
    "test_plugin_readonly_loading", "test_native_current_interrupt", "test_signed_windows_rebind",
    "test_native_capture_duration", "test_native_settings_packaging", "test_beta3_release_contract",
    "test_native_drain_progress",
    "test_native_drain_order",
    "test_native_unserved_settings",
    "test_native_corrections",
    "test_native_lifetime",
    "test_native_input_gap",
    "test_native_early_finish",
    "test_native_fault_scope",
    "test_plugin_sdk_declaration",
    "test_native_output_only",
    "test_native_meeting_endurance",
    "test_speaker_attribution_contract", "test_reused_release_assets",
    "test_plugin_sdk_pin",
    "test_native_rebuild_libraries",
    "test_native_metal_library_beside",
    "test_release_gates_refuse_optimised_python",
    "test_native_abort_while_opening",
    "test_native_reply_voice",
    "test_carrier_with_fixture_worker",
)

# What neither gate can run: for each module, why, and where it does run.
OUTSIDE_THE_GATES = {
    "test_apple_pocket_cancel_contract":
        "One of its two tests parses the Swift bridge with swiftc and skips where there is no Swift "
        "toolchain, and both gates count a skip as a failure. No gate runs it. It runs whole on a Mac "
        "with Xcode, by hand: python -m pytest tests/test_apple_pocket_cancel_contract.py",
    "test_separator_export":
        "It imports torch, which requirements-test.txt does not install. It runs in the model-export "
        "environment (torch, onnx, onnxruntime) when the separator's export changes: "
        "python -m unittest tests.test_separator_export -v (docs/DEVELOPMENT.md, docs/OVERLAP_IDENTITY_GATE.md)",
    "test_voice_app_conversation":
        "It imports runtime.voice_app, which imports aiohttp, and requirements-test.txt does not install "
        "aiohttp. No gate runs it. It runs only where the Python voice application's own dependencies "
        "are installed.",
}

# THE PYTHON DOUBLE. The engine that ships is the native worker. The Python engine is
# retired from every package and kept in the tree as a double of it for tests. These are
# the test modules, in either gate or outside both, that import or start the Python
# engine's code: runtime/plugin_engine and what its worker imports. For each, what of
# it the module exercises. A module here may exercise
# what ships as well, and then its sentence says so. A module that is not here runs none
# of the Python engine. A count of passed tests is split with this: the result of a run
# states how many of its cases belong to these modules.
PYTHON_DOUBLE = {
    "test_native_async": "runtime.stt.native_async, the Python engine's recognizer stream",
    "test_native_endpoint_backend": "runtime.native_endpoint, the Python engine's binding of the endpoint library",
    "test_native_pocket_backend": "runtime.native_pocket.backend, the Python engine's binding of the synthesizer library",
    "test_native_pocket_profile": "runtime.native_pocket.profile and runtime.windows_voice.native_pocket, the Python engine's Windows composition",
    "test_native_preview": "runtime.stt.native_preview",
    "test_native_profile": "runtime.stt.profile, the double's choice of its recognizer's catalog from a runtime profile, with runtime.model_assets",
    "test_native_resident": "runtime.stt.native_resident",
    "test_native_stream": "runtime.voice_core.native_stream",
    "test_native_streaming": "runtime.stt.native_streaming",
    "test_native_uid_snapshot": "runtime.speaker_identity, the Python enrollment codec, held to the native codec's probe: both are exercised",
    "test_plugin_abort_drain": "runtime.plugin_engine.session, in process",
    "test_plugin_capture_processing": "runtime.plugin_engine.capture and runtime.plugin_engine.session, in process",
    "test_plugin_cuda_control": "runtime.plugin_engine.session, in process",
    "test_plugin_engine": "runtime.plugin_engine (the session, the audio framing, the MLX adapter) and runtime.speech_output, in process",
    "test_plugin_input_completion": "runtime.plugin_engine.session, in process",
    "test_plugin_playback_control": "runtime.plugin_engine.session, in process",
    "test_plugin_playback_receipts": "runtime.plugin_engine.session, in process",
    "test_plugin_playback_wire": "the Python worker tests/plugin_worker_fixture.py, behind the real carrier and the proof host",
    "test_plugin_process": "the Python worker tests/plugin_worker_fixture.py, behind the real carrier and the proof host",
    "test_plugin_readiness": "runtime.plugin_engine.readiness; one of its tests is of the proof host alone",
    "test_plugin_readonly_loading": "runtime.plugin_engine.worker's model loading, runtime.windows_voice, runtime.cuda_voice and runtime.speech_output",
    "test_plugin_worker_cleanup_exit": "the Python worker tests/plugin_worker_fixture.py, started by itself",
    "test_python_carrier_liveness": "runtime.plugin_engine.worker's watch of its carrier, in a process of its own",
    "test_python_speaker_amendment": "runtime.plugin_engine.speaker_event",
    "test_sdk_host_construction": "it imports runtime.plugin_engine.worker to replace one function of it and starts no worker; what it proves is the proof host's custody of its pipes",
    "test_semantic_endpoint_late": "runtime.voice_core.semantic_endpoint",
    "test_stt_evidence_client_close": "runtime.stt.evidence_client",
    "test_voice_app_conversation": "runtime.voice_app.conversation",
    "test_voice_core_gapless_per_stream": "runtime.voice_core.live",
    "test_voice_core_playback_after_cancel": "runtime.voice_core.live",
    "test_voice_core_trace_order": "runtime.voice_core.live, with the tests' model double",
}

# THE FLOOR. Tests must not be lost unseen: a module dropped from TESTS, or tests gone from
# a module, has to fail the gate. A run must hold at least this many test cases. The number
# is what TESTS collects on Linux (python -m pytest --collect-only over its 51 modules).
# Raise it with every test added. It was 239 beside runs of more than 600.
#
# It counts the report's <testcase> elements and not the report's "tests" total. The total
# also counts unittest subtests where the installed pytest reports them, so it differs
# between machines for the same tests: gate runs have said 609 for 605 cases, and those
# 605 were exactly the cases that day's list collected on Linux.
MINIMUM_CASES = 727

# THE TIME LIMIT of the whole pytest run, in seconds, where --time-limit gives no other. It
# was 300 typed in the call, and a run that reached it ended in a traceback and no result.
#
# Measured, not chosen. On the Apple Silicon machine the gate is run on, this list took
# 242 s when it held 727 cases; on Linux, where the three modules that need the carrier's
# build are left out, 175 s. The default is twice the run on the gate's machine, rounded up
# to a whole minute. Every result states its own seconds: when the list grows by more than a
# few cases, set this again from a whole run there.
DEFAULT_TIME_LIMIT = 540


def validate_environment(version=None, find_spec=importlib.util.find_spec):
    version = sys.version_info if version is None else version
    if version < (3, 11):
        raise ValueError("source gate requires Python 3.11+; see requirements-test.txt")
    missing = [name for name in ("pytest", "pytest_asyncio", "numpy", "jsonschema") if find_spec(name) is None]
    if missing:
        raise ValueError("source gate dependencies missing: " + ", ".join(missing)
                         + "; install requirements-test.txt in an isolated environment")


def run_bounded(command, *, cwd, env, log, limit):
    """Run the gate's command inside its time limit: (exit code or None, timed out, seconds).

    At the limit the run is ended and has failed. That is an outcome the gate records, not a
    traceback: exit code None, timed out True.
    """
    began = time.monotonic()
    try:
        code, timed_out = subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                         timeout=limit).returncode, False
    except subprocess.TimeoutExpired:
        code, timed_out = None, True
    return code, timed_out, round(time.monotonic() - began, 1)


def report_counts(xml):
    """What a run's report holds: its totals, its test cases, and how many are the Python double's."""
    counts = dict(tests=0, failures=0, errors=0, skipped=0)
    cases = double = 0
    if Path(xml).is_file():
        root = ET.parse(xml).getroot()
        for suite in root.iter("testsuite"):
            for key in counts:
                counts[key] += int(suite.get(key, "0"))
        for case in root.iter("testcase"):
            cases += 1
            module = (case.get("classname") or "").split(".")
            double += len(module) > 1 and module[0] == "tests" and module[1] in PYTHON_DOUBLE
    return counts, cases, double


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--carrier-build", type=Path, required=True)
    parser.add_argument("--native-worker", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--time-limit", type=float, default=DEFAULT_TIME_LIMIT, metavar="SECONDS",
                        help="how long the whole pytest run may take (default: %(default)s)")
    args = parser.parse_args()
    validate_environment()
    if not args.time_limit > 0:
        raise ValueError("the time limit is a positive number of seconds")
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
        exit_code, timed_out, seconds = run_bounded(command, cwd=ROOT, env=env, log=log, limit=args.time_limit)
    counts, cases, double = report_counts(out / "pytest.xml")
    passed = (exit_code == 0 and cases >= MINIMUM_CASES
              and not any(counts[k] for k in ("failures", "errors", "skipped")))
    result = dict(passed=passed, scope=__doc__, exit_code=exit_code,
                  counts=counts, cases=cases, minimum_cases=MINIMUM_CASES,
                  python_double_cases=double, other_cases=cases - double,
                  seconds=seconds, time_limit_seconds=args.time_limit, timed_out=timed_out,
                  test_files=list(TESTS), command=command,
                  release_qualified=False)
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
