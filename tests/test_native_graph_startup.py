from types import SimpleNamespace as NS

import pytest

from scripts.prove_native_graph_startup import configure
from scripts.audit_native_graph_startup import compare, construction, ORDER, CONTRACT, BASE_SOURCE, BASE_RUNTIME


def settings():
    return NS(GraphOptimizationLevel=NS(ORT_ENABLE_ALL="all", ORT_DISABLE_ALL="off")), NS(
        graph_optimization_level="all", intra_op_num_threads=4, inter_op_num_threads=1)


@pytest.mark.parametrize("arm", ["baseline-1", "candidate-1", "candidate-2", "baseline-2"])
@pytest.mark.parametrize("part", ["encoder", "decoder", "joiner"])
def test_only_candidate_encoder_changes(arm, part):
    ort, options = settings()
    before = vars(options).copy()
    provider = ["DmlExecutionProvider"] if part == "encoder" else ["CPUExecutionProvider"]
    assert configure(ort, options, part + ".onnx", provider, arm) == part
    if arm.startswith("candidate") and part == "encoder":
        before["graph_optimization_level"] = "off"
    assert vars(options) == before


@pytest.mark.parametrize("change", ["provider", "threads", "level", "graph", "arm"])
def test_changed_baseline_refused(change):
    ort, options = settings()
    provider, part, arm = ["DmlExecutionProvider"], "encoder", "baseline-1"
    if change == "provider":
        provider = ["CPUExecutionProvider"]
    elif change == "threads":
        options.intra_op_num_threads = 1
    elif change == "level":
        options.graph_optimization_level = "off"
    elif change == "graph":
        part = "other"
    else:
        arm = "candidate-3"
    with pytest.raises(ValueError):
        configure(ort, options, part + ".onnx", provider, arm)


def arms():
    return [dict(name=n, construction_seconds=20 if n.startswith("baseline") else 16,
                 encoder_seconds=[0.1] * 33, first_partial_seconds=[1] * 3,
                 finish_seconds=[0.2] * 3) for n in ORDER]


def test_complete_passing_comparison_is_not_promotion():
    r = compare(arms())
    assert r["verdict"] == "earn_full_runtime_comparison"
    assert r["seconds_saved"] == 4 and not r["qualified"] and not r["runtime_promoted"]


@pytest.mark.parametrize("field,value,gate", [
    ("construction_seconds", 19.5, "construction"),
    ("encoder_seconds", [0.11]*33, "encoder"),
    ("first_partial_seconds", [1.1]*3, "first_partial"),
    ("finish_seconds", [0.23]*3, "finish"),
])
def test_each_speed_gate_can_refuse(field, value, gate):
    rows = arms()
    for row in rows[1:3]:
        row[field] = value
    r = compare(rows)
    assert r["verdict"] == "do_not_adopt" and not r["gates"][gate]


def test_missing_or_reordered_arm_is_not_comparison():
    for rows in (arms()[:3], arms()[::-1]):
        with pytest.raises(ValueError, match="arm order"):
            compare(rows)


def constructor_fixture():
    before = dict(system_kernel_seconds=5, system_user_seconds=5, system_idle_seconds=5,
                  processes={"recognizer": dict(pid=42, creation_seconds=10, kernel_seconds=1, user_seconds=1)})
    after = dict(system_kernel_seconds=5, system_user_seconds=6, system_idle_seconds=5,
                 processes={"recognizer": dict(pid=42, creation_seconds=10, kernel_seconds=1, user_seconds=2)})
    owner = dict(name="candidate-1", pid=42, exit_code=0, timed_out=False, passed=True)
    rows = [dict(graph=p, seconds=10, intra_threads=4, inter_threads=1,
                 providers=["DmlExecutionProvider"] if p == "encoder" else ["CPUExecutionProvider"],
                 optimization_level="GraphOptimizationLevel.ORT_DISABLE_ALL" if p == "encoder" else "GraphOptimizationLevel.ORT_ENABLE_ALL",
                 counter_before=before, counter_after=after, process_cpu_seconds=1, system_busy_mean_cores=0.1)
            for p in ("encoder", "decoder", "joiner")]
    record = dict(name="candidate-1", contract=CONTRACT, runtime_manifest_sha256=BASE_RUNTIME,
                  source_manifest_sha256=BASE_SOURCE, onnxruntime="1.24.4", passed=True,
                  qualified=False, post_source_runtime_readback=True, graphs=rows)
    return record, owner


def test_constructor_evidence_passes():
    record, owner = constructor_fixture()
    assert construction(record, owner) == 30


@pytest.mark.parametrize("damage", ["unapplied", "decoder_changed", "provider", "cpu", "pid", "timeout", "source", "census"])
def test_bad_constructor_evidence_refused(damage):
    record, owner = constructor_fixture()
    if damage == "unapplied":
        record["graphs"][0]["optimization_level"] = "GraphOptimizationLevel.ORT_ENABLE_ALL"
    elif damage == "decoder_changed":
        record["graphs"][1]["optimization_level"] = "GraphOptimizationLevel.ORT_DISABLE_ALL"
    elif damage == "provider":
        record["graphs"][0]["providers"] = ["CPUExecutionProvider"]
    elif damage == "cpu":
        record["graphs"][0]["process_cpu_seconds"] = 0
    elif damage == "pid":
        owner["pid"] = 43
    elif damage == "timeout":
        owner["timed_out"] = True
    elif damage == "source":
        record["source_manifest_sha256"] = "wrong"
    else:
        record["graphs"].pop()
    with pytest.raises(ValueError):
        construction(record, owner)
