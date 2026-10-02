"""Integration repairs must preserve integrity, exact selection and honest gates."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.assemble_guided_beta_candidate import uid_replacement
from scripts.package_native_runtime import runtime_inventory, sha256, verify
from scripts.validate_source_closeout import validate_environment


def test_source_gate_refuses_missing_test_prerequisites_before_running():
    with pytest.raises(ValueError, match="Python 3.11"):
        validate_environment((3, 9), lambda name: object())
    with pytest.raises(ValueError, match="pytest_asyncio"):
        validate_environment((3, 12), lambda name: None if name == "pytest_asyncio" else object())
    validate_environment((3, 12), lambda name: object())


@pytest.mark.parametrize("qualified", [True, False])
def test_qualification_label_never_bypasses_inventory(tmp_path, qualified):
    payload = tmp_path / "worker"
    payload.write_bytes(b"exact runtime")
    manifest = tmp_path / "voice-runtime.json"
    manifest.write_text(json.dumps(dict(schema="aiii.voice.native-runtime",
        platform="darwin", qualified=qualified, files=runtime_inventory(tmp_path))))
    pin = sha256(manifest)
    assert verify(tmp_path, pin)["qualified"] is qualified
    payload.write_bytes(b"other runtime")
    with pytest.raises(ValueError, match="payload differs"):
        verify(tmp_path, pin)


@pytest.mark.parametrize("qualified", [None, "true", 0, 1])
def test_qualification_metadata_is_strictly_typed(tmp_path, qualified):
    manifest = tmp_path / "voice-runtime.json"
    manifest.write_text(json.dumps(dict(schema="aiii.voice.native-runtime",
        platform="darwin", qualified=qualified, files={})))
    with pytest.raises(ValueError, match="unsupported runtime"):
        verify(tmp_path, sha256(manifest))


def uid_inputs(tmp_path):
    old = dict(path="uid/model.onnx", name="uid", sha256="a" * 64, size=100)
    new = dict(old, sha256="b" * 64, size=80)
    cfg = dict(id="id.aiii.voice", version="0.1.0-beta.2", models=[old,
        dict(path="asr/model", name="asr", sha256="c" * 64, size=200)])
    model = copy.deepcopy(cfg)
    model["models"][0] = new
    template = tmp_path / "models.json"
    template.write_text(json.dumps(model))
    notices = tmp_path / "notices"
    notices.mkdir()
    raw = b"original terms"
    (notices / "LICENSE").write_bytes(raw)
    record = dict(model_sha256=new["sha256"], model_bytes=new["size"],
        replaces_model_sha256=old["sha256"], declarations=["original terms"],
        files={"LICENSE": dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())})
    (notices / "UID-REPLACEMENT.json").write_text(json.dumps(record))
    index = dict(models=[copy.deepcopy(old)], open_items=[
        "WeSpeaker delegates model licensing to training datasets; VoxBlink2 old disposition",
        "another component remains open"])
    return cfg, index, template, notices


def test_uid_replacement_changes_only_bound_model_and_notices(tmp_path):
    cfg, index, template, notices = uid_inputs(tmp_path)
    asr = copy.deepcopy(cfg["models"][1])
    files = uid_replacement(cfg, index, template, notices)
    assert cfg["models"][1] == asr
    assert cfg["models"][0]["sha256"] == "b" * 64
    assert index["open_items"] == ["another component remains open"]
    assert files["notices/uid-resnet152-lm/LICENSE"] == b"original terms"


@pytest.mark.parametrize("damage", ["other_model", "duplicate", "notice", "cross_model", "late_index"])
def test_uid_replacement_cannot_mix_models_or_stale_notices(tmp_path, damage):
    cfg, index, template, notices = uid_inputs(tmp_path)
    original = copy.deepcopy(cfg)
    model = json.loads(template.read_text())
    if damage == "other_model": model["models"][1]["sha256"] = "d" * 64
    elif damage == "duplicate": model["models"].append(model["models"][0])
    elif damage == "notice": (notices / "LICENSE").write_bytes(b"tampered")
    elif damage == "late_index": index["open_items"] = []
    else: model["models"][0]["sha256"] = "d" * 64
    original_index = copy.deepcopy(index)
    template.write_text(json.dumps(model))
    with pytest.raises(ValueError): uid_replacement(cfg, index, template, notices)
    assert cfg == original
    assert index == original_index


def test_selected_skips_fail_the_gate_and_missing_tests_are_not_hidden(tmp_path):
    gate = Path(__file__).with_name("conftest.py")
    (tmp_path / "conftest.py").write_bytes(gate.read_bytes())
    test = tmp_path / "test_one.py"
    test.write_text("import pytest\ndef test_skipped(): pytest.skip('missing required input')\n")
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "--fail-on-skips", str(test)],
                         cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert run.returncode == 1 and "1 skipped" in run.stdout, run.stdout + run.stderr
    test.write_text("from scripts.missing_required_helper import missing\n")
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", str(test)],
                         cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert run.returncode == 2 and "ERROR collecting" in run.stdout, run.stdout + run.stderr
