"""Falsify the reuse claim against retained actual-model cycles."""

import copy
import json
from pathlib import Path

import pytest

from scripts.audit_plugin_resident_reuse import audit, check_directml_identity

ROOT = (
    Path(__file__).resolve().parents[1]
    / "deliverables/plugin-sdk-engine-20260908/mlx-resident-reuse-r8"
)


def test_real_resident_cycles_are_source_bound_and_current():
    assert audit(ROOT)["passed"]


@pytest.mark.parametrize(
    "mutation", ["foreign_event", "reload", "tail", "process_exit", "stale_control"]
)
def test_resident_evidence_falsifiers(mutation):
    reports = [
        json.loads((ROOT / f"session-{n}/report.json").read_text()) for n in (1, 2)
    ]
    summary = json.loads((ROOT / "reuse.json").read_text())
    if mutation == "foreign_event":
        reports[1]["events"][0]["session_id"] = "resident-reuse-1"
    elif mutation == "reload":
        ready = next(e for e in reports[1]["events"] if e["type"] == "session_ready")
        # Alter BOTH startup claims and actual-session observations so a single
        # internally consistent but reloaded model cannot pass as reuse.
        for field in ("warm_readiness", "live_residency"):
            ready["models"][field] = copy.deepcopy(ready["models"][field])
            ready["models"][field]["stt_pid"] += 1
    elif mutation == "tail":
        reports[1]["final_snapshot"]["input"]["processed_end_sample"] -= 1
    elif mutation == "process_exit":
        summary["exit_code"] = 1
    else:
        summary["old_session_control_refused"] = ""
    with pytest.raises(AssertionError):
        audit(ROOT, reports, summary)


def native_identity():
    return {"backend":"windows-pocket-vulkan-stt-directml",
            "models":{"stt":{"providers":{"encoder":["DmlExecutionProvider"]}},
                      "tts":{"backend":"native-pocket-vulkan", "model_layout":"host_data_runtime_config",
                             "library_sha256":"a"*64}},
            "warm_readiness":{"accelerators":{"stt":"directml","tts":"vulkan","endpoint":"cpu"}}}


def test_native_tts_identity_extends_not_replaces_approved_cpu_identity():
    check_directml_identity(native_identity())
    value = native_identity()
    value["backend"] = "windows-pocket-cpu-stt-directml"
    del value["models"]["tts"]
    del value["warm_readiness"]["accelerators"]
    check_directml_identity(value)


@pytest.mark.parametrize('damage',['stt-cpu','tts-cpu','unbound-library','component-layout','unclaimed-device','automatic'])
def test_native_execution_identity_cannot_mislabel_a_cpu_or_unbound_component(damage):
    value = native_identity()
    if damage == 'stt-cpu': value['models']['stt']['providers']['encoder'] = ['CPUExecutionProvider']
    elif damage == 'tts-cpu': value['models']['tts']['backend'] = 'native-pocket-cpu'
    elif damage == 'unbound-library': value['models']['tts']['library_sha256'] = ''
    elif damage == 'component-layout': value['models']['tts']['model_layout'] = 'component_bundle'
    elif damage == 'unclaimed-device': value['warm_readiness']['accelerators']['tts'] = 'cpu'
    elif damage == 'automatic': value['backend'] = 'automatic'
    with pytest.raises(AssertionError):
        check_directml_identity(value)
