import ast
import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from runtime.model_assets import ModelAssets
from runtime.stt import native_assets as native
from scripts import prepare_native_model_assets as builder


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    checkpoint = source / "model.safetensors"
    values = np.arange(640, dtype="<u4").tobytes()
    checkpoint.write_bytes(b"HEADER00" + values)
    digest, size = sha(checkpoint.read_bytes()), checkpoint.stat().st_size
    rows = [
        {
            "initializer": f"weight{i}",
            "reference": f"source{i}",
            "offset": 8 + 4 * i,
            "bytes": 4,
            "reference_sha256": sha(values[4 * i : 4 * i + 4]),
        }
        for i in range(640)
    ]
    (source / "weight-view.json").write_text(
        json.dumps(
            {
                "checkpoint_sha256": digest,
                "checkpoint_bytes": size,
                "initializers": rows,
            }
        )
    )
    for name in ("encoder.onnx", "decoder.onnx", "joiner.onnx", "tokens.txt"):
        (source / name).write_bytes(name.encode())
    pins = {p.name: (p.stat().st_size, sha(p.read_bytes())) for p in source.iterdir()}
    monkeypatch.setattr(native, "PINNED_FILES", pins)
    monkeypatch.setattr(builder, "PINNED_FILES", pins)
    monkeypatch.setattr(native, "CHECKPOINT_SHA", digest)
    monkeypatch.setattr(builder, "CHECKPOINT_SHA", digest)
    monkeypatch.setattr(native, "CHECKPOINT_BYTES", size)
    mel = np.arange(128 * 257, dtype="<f4").reshape(128, 257) / np.float32(1234)
    monkeypatch.setattr(native, "MEL_SHA", sha(mel.tobytes()))
    monkeypatch.setattr(builder, "MEL_SHA", sha(mel.tobytes()))
    features = source / "features.npz"
    np.savez(features, mel_filters=mel, not_shipped=np.ones(100))
    monkeypatch.setattr(builder, "FEATURES_SHA", sha(features.read_bytes()))
    return source, checkpoint, features, mel


def stage(inputs, tmp_path):
    source, checkpoint, features, _ = inputs
    resources, data = tmp_path / "resources", tmp_path / "data"
    result = builder.prepare(source, features, checkpoint, resources, data)
    return resources, data, result


def test_runtime_owned_staging_relocates_without_proof_inputs(inputs, tmp_path):
    resources, data, result = stage(inputs, tmp_path)
    assert result["files"] == 7 and result["weight_bytes_copied"] == 0
    assert result["qualified"] is False
    assert not list(data.rglob("*.npz")) and not list(data.rglob("*.py"))
    renamed_resources, renamed_data = (
        tmp_path / "relocated-resources",
        tmp_path / "relocated-data",
    )
    resources.rename(renamed_resources)
    data.rename(renamed_data)
    assets = ModelAssets(
        renamed_resources / "catalog.json", renamed_data, backend="windows-pocket"
    )
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}
    bound = native.load_installed(assets)
    assert bound.identity["verified_encoder_tensors"] == 640
    assert bound.identity["asset_layout"] == "installed-model-assets"
    assert bound.encoder_path == renamed_data / "stt-native/encoder.onnx"
    assert "encoder_artifact" not in bound.identity
    assert bound.mel.tobytes() == inputs[-1].tobytes()
    assert not bound.mel.flags.writeable
    assert before == {p: p.stat().st_mtime_ns for p in before}


def add_precomputed(resources, data, monkeypatch):
    """Coherent, tiny data binding: substitutions must fail at the loader pins."""
    pins = {}
    manifest_path = resources / "manifests/stt-native.json"
    manifest = json.loads(manifest_path.read_text())
    catalog_path = resources / "catalog.json"
    catalog = json.loads(catalog_path.read_text())
    group = catalog["groups"]["stt-native"]
    for name in native.PRECOMPUTED_FILES:
        raw = ("bound " + name).encode()
        (data / "stt-native" / name).write_bytes(raw)
        pins[name] = (len(raw), sha(raw))
        manifest["files"].append({"path": name, "size": len(raw), "lfs_sha256": sha(raw)})
        group["files"][name] = {"bytes": len(raw), "sha256": sha(raw)}
    manifest_path.write_text(json.dumps(manifest))
    group["manifest_sha256"] = sha(manifest_path.read_bytes())
    catalog_path.write_text(json.dumps(catalog))
    monkeypatch.setattr(native, "PRECOMPUTED_FILES", pins)
    return ModelAssets(catalog_path, data, backend="windows-pocket")


def test_precomputed_selection_keeps_all_source_tensor_checks(inputs, tmp_path, monkeypatch):
    resources, data, _ = stage(inputs, tmp_path)
    assets = add_precomputed(resources, data, monkeypatch)
    bound = native.load_installed(assets)
    assert bound.encoder_path == data / "stt-native/encoder.optimized.onnx"
    assert bound.identity["verified_encoder_tensors"] == 640
    assert "source checkpoint spans" in bound.identity["tensor_verification_scope"]
    # A substituted verifier must still be called, even with a valid cache.
    def reject(*_):
        raise ValueError("source tensor readback required")
    monkeypatch.setattr(native, "verify_weights", reject)
    with pytest.raises(ValueError, match="source tensor readback required"):
        native.load_installed(assets)


@pytest.mark.parametrize("damage", ["partial", "other-backend", "pin", "data", "undeclared"])
def test_precomputed_is_exact_declared_and_never_a_fallback(inputs, tmp_path, monkeypatch, damage):
    resources, data, _ = stage(inputs, tmp_path)
    if damage == "undeclared":
        (data / "stt-native/weights.bin").write_bytes(b"not catalogued")
        assets = ModelAssets(resources / "catalog.json", data, backend="windows-pocket")
    else:
        assets = add_precomputed(resources, data, monkeypatch)
        if damage == "partial":
            del assets.groups["stt-native"]["files"]["weights.bin"]
        elif damage == "other-backend":
            assets.backend = "mlx"
        elif damage == "pin":
            native.PRECOMPUTED_FILES["weights.bin"] = (1, "0" * 64)
        elif damage == "data":
            p = data / "stt-native/weights.bin"
            p.write_bytes(b"x" * p.stat().st_size)
    with pytest.raises(ValueError):
        native.load_installed(assets)


def test_resident_consumes_selected_encoder_not_implicit_original():
    source = (Path(__file__).resolve().parents[1] / "runtime/stt/native_resident.py").read_text()
    calls = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "NemoOnnxRecognizer"]
    assert len(calls) == 1
    value = next(k.value for k in calls[0].keywords if k.arg == "aligned_encoder")
    assert ast.unparse(value) == "bound.encoder_path"


@pytest.mark.parametrize(
    "damage",
    [
        "omit",
        "duplicate",
        "offset",
        "zero",
        "negative",
        "boolean",
        "hash",
        "checkpoint",
    ],
)
def test_tensor_readback_refuses_corrupt_identity_or_extent(inputs, damage):
    source, checkpoint, _, _ = inputs
    view = source / "weight-view.json"
    body = json.loads(view.read_text())
    if damage == "omit":
        body["initializers"].pop()
    elif damage == "duplicate":
        body["initializers"][-1] = copy.deepcopy(body["initializers"][0])
    elif damage == "checkpoint":
        checkpoint.write_bytes(checkpoint.read_bytes()[:-1] + b"X")
    else:
        body["initializers"][0].update(
            {
                "offset": {"offset": checkpoint.stat().st_size},
                "zero": {"bytes": 0},
                "negative": {"bytes": -4},
                "boolean": {"offset": True},
                "hash": {"reference_sha256": "0" * 64},
            }[damage]
        )
    view.write_text(json.dumps(body))
    with pytest.raises(ValueError):
        native.verify_weights(source, checkpoint)


@pytest.mark.parametrize(
    "damage", ["count", "nan", "negative", "changed", "float", "extra", "encoding"]
)
def test_filters_cannot_move_or_lose_coefficients(inputs, tmp_path, damage):
    resources, data, _ = stage(inputs, tmp_path)
    path = data / "stt-native/mel-filters.json"
    body = json.loads(path.read_text())
    if damage == "count":
        body["values"].pop()
    elif damage == "extra":
        body["script"] = "pass"
    elif damage == "encoding":
        body["encoding"] = "pickle"
    else:
        body["values"][0] = {
            "nan": 0x7FC00000,
            "negative": 0xBF800000,
            "changed": 1,
            "float": 0.0,
        }[damage]
    path.write_text(json.dumps(body))
    with pytest.raises(ValueError):
        native.filters(path)


def test_pin_cannot_be_changed_by_a_new_catalog(inputs, tmp_path):
    resources, data, _ = stage(inputs, tmp_path)
    path = data / "stt-native/encoder.onnx"
    # Replace the lab link, never change the source it points to. Give the new
    # bytes a coherent catalog AND manifest: only the native model pin can stop
    # this substitution, not an incidental catalog checksum mismatch.
    path.unlink()
    content = b"different yet coherently catalogued graph"
    path.write_bytes(content)
    manifest_path = resources / "manifests/stt-native.json"
    manifest = json.loads(manifest_path.read_text())
    row = next(r for r in manifest["files"] if r["path"] == "encoder.onnx")
    row.update(
        size=len(content),
        git_blob=hashlib.sha1(
            f"blob {len(content)}\0".encode() + content, usedforsecurity=False
        ).hexdigest(),
    )
    manifest_path.write_text(json.dumps(manifest))
    catalog = resources / "catalog.json"
    body = json.loads(catalog.read_text())
    group = body["groups"]["stt-native"]
    group["manifest_sha256"] = sha(manifest_path.read_bytes())
    group["files"]["encoder.onnx"] = {"bytes": len(content), "sha256": sha(content)}
    catalog.write_text(json.dumps(body))
    assets = ModelAssets(resources / "catalog.json", data, backend="windows-pocket")
    assert assets.snapshot("stt-native") == data / "stt-native"
    with pytest.raises(ValueError, match="binding differs"):
        native.load_installed(assets)


def test_missing_or_extra_installed_data_is_refused(inputs, tmp_path):
    resources, data, _ = stage(inputs, tmp_path)
    assets = ModelAssets(resources / "catalog.json", data, backend="windows-pocket")
    (data / "stt-native/research.py").write_text("must not execute")
    with pytest.raises(ValueError):
        native.load_installed(assets)


def test_no_research_imports_on_native_startup():
    root = Path(__file__).resolve().parents[1]
    for name in ("runtime/stt/native_assets.py", "runtime/stt/native_resident.py"):
        tree = ast.parse((root / name).read_text())
        for node in ast.walk(tree):
            modules = (
                [node.module]
                if isinstance(node, ast.ImportFrom)
                else [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            assert all(
                not m or not m.startswith(("scripts", "experiments", "tests"))
                for m in modules
            )


def test_staging_is_create_only_and_refuses_corrupt_source(inputs, tmp_path):
    source, checkpoint, features, _ = inputs
    (source / "encoder.onnx").write_bytes(b"wrong graph")
    resources, data = tmp_path / "resources", tmp_path / "data"
    with pytest.raises(ValueError):
        builder.prepare(source, features, checkpoint, resources, data)
    assert not data.exists() and not resources.exists()


def test_tensor_census_uses_one_sequential_checkpoint_read(inputs, monkeypatch):
    # The candidate failed the complete speech performance gate. Keep its
    # mechanism test on the isolated experimental module, not the ship source.
    from experiments import native_assets_single_read as candidate

    source, checkpoint, _, _ = inputs
    monkeypatch.setattr(candidate, "CHECKPOINT_BYTES", native.CHECKPOINT_BYTES)
    monkeypatch.setattr(candidate, "CHECKPOINT_SHA", native.CHECKPOINT_SHA)
    fdopen = native.os.fdopen
    read_bytes, opens = [], []

    class Counted:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def fileno(self):
            return self.handle.fileno()

        def read(self, count):
            assert 0 < count <= 8 * 1024 * 1024
            data = self.handle.read(count)
            read_bytes.append(len(data))
            return data

        def seek(self, *_args):
            pytest.fail("checkpoint verification must not reread tensor extents")

    def observed(fd, *args, **kwargs):
        handle = fdopen(fd, *args, **kwargs)
        if native.os.fstat(fd).st_size == native.CHECKPOINT_BYTES:
            opens.append(fd)
            return Counted(handle)
        return handle

    monkeypatch.setattr(native.os, "fdopen", observed)
    assert candidate.verify_weights(source, checkpoint) == 640
    assert len(opens) == 1
    assert sum(read_bytes) == native.CHECKPOINT_BYTES


def test_ranges_cross_chunks_overlap_and_arrive_out_of_order(inputs, monkeypatch):
    source, checkpoint, _, _ = inputs
    data = b"HEADER00" + bytes(range(256)) * 33000 + b"TAIL"
    checkpoint.write_bytes(data)
    view = source / "weight-view.json"
    body = json.loads(view.read_text())
    for i, row in enumerate(body["initializers"]):
        start = 8 * 1024 * 1024 - 16 + (i % 8)
        size = 128 + i
        row.update(
            offset=start, bytes=size, reference_sha256=sha(data[start : start + size])
        )
    body["initializers"].reverse()
    body.update(checkpoint_sha256=sha(data), checkpoint_bytes=len(data))
    view.write_text(json.dumps(body))
    monkeypatch.setattr(native, "CHECKPOINT_SHA", sha(data))
    monkeypatch.setattr(native, "CHECKPOINT_BYTES", len(data))
    assert native.verify_weights(source, checkpoint) == 640
    # Neither the header nor the unreferenced tail is in any tensor span.
    # Full-file checking must still refuse a same-size corruption there.
    for offset in (0, len(data) - 1):
        damaged = bytearray(data)
        damaged[offset] ^= 1
        checkpoint.write_bytes(damaged)
        with pytest.raises(ValueError, match="Installed checkpoint differs"):
            native.verify_weights(source, checkpoint)
