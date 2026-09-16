"""Portable, byte-exact source and checkpoint verification for exported runtimes."""

import hashlib
import json
from pathlib import Path


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_tts(stage, export_sha):
    # Bind the original checkpoint manifest, not the Linux benchmark's imports.
    MANIFEST_HASH = "ae5f6a89c818172226f0a1f9937cb1098786d2a86c3a09a319e84fb91286305c"

    root = stage / "tts"
    manifest = root / "cuda_tts/qwen3_tts_reference_manifest.json"
    if digest(manifest) != MANIFEST_HASH:
        raise ValueError("Original TTS manifest differs")
    body = json.loads(manifest.read_text())
    export = stage / "tts-source-export.json"
    if digest(export) != export_sha:
        raise ValueError("TTS source export manifest differs")
    expected_source = json.loads(export.read_text())
    if expected_source["source"] != body["source"]:
        raise ValueError("TTS export upstream source identity differs")
    source = root / "Qwen3-TTS"
    actual = {
        p.relative_to(source).as_posix(): digest(p)
        for p in source.rglob("*")
        if p.is_file()
        and ".git" not in p.relative_to(source).parts
        and "__pycache__" not in p.parts
        and p.suffix != ".pyc"
    }
    if actual != expected_source["files"]:
        raise ValueError("TTS exported source bytes/file set differ")
    snapshot = (
        root
        / "hf-home/hub/models--Qwen--Qwen3-TTS-12Hz-0.6B-Base/snapshots"
        / body["model"]["revision"]
    )
    expected = {row["path"]: row for row in body["model"]["files"]}
    actual_names = {
        p.relative_to(snapshot).as_posix() for p in snapshot.rglob("*") if p.is_file()
    }
    if actual_names != set(expected):
        raise ValueError("TTS snapshot file set differs")
    for name, row in expected.items():
        path = snapshot / name
        if path.stat().st_size != row["size"]:
            raise ValueError("TTS snapshot size differs: " + name)
        if "lfs_sha256" in row:
            actual_hash, wanted = digest(path), row["lfs_sha256"]
        else:
            blob = hashlib.sha1(f"blob {row['size']}\0".encode())
            blob.update(path.read_bytes())
            actual_hash, wanted = blob.hexdigest(), row["git_blob"]
        if actual_hash != wanted:
            raise ValueError("TTS snapshot identity differs: " + name)
    reference = json.loads((root / "cuda_tts/reference_fixture.json").read_text())
    audio = stage / "84-121123-0018.wav"
    if digest(audio) != reference["audio_sha256"]:
        raise ValueError("Public conditioning audio differs")
    return (
        snapshot,
        audio,
        reference,
        {
            "upstream_manifest_sha256": MANIFEST_HASH,
            "source_export_sha256": export_sha,
            "source": body["source"],
            "model": body["model"],
        "runtime_contract": "explicit Windows FP16 talker with FP32 predictor residual path and codec; not the Ubuntu BF16 qualification",
        },
    )


def residency(
    model, expected_talker_dtype, expected_codec_dtype, *, fp32_predictor=False
):
    groups = {"talker": model.model, "codec": model.model.speech_tokenizer.model}
    result = {}
    for name, module in groups.items():
        expected = expected_talker_dtype if name == "talker" else expected_codec_dtype
        devices, dtypes = {}, {}
        precision_errors = []
        embeddings = (
            {
                id(p)
                for emb in model.model.talker.code_predictor.get_input_embeddings()
                for p in emb.parameters()
            }
            if fp32_predictor
            else set()
        )
        for parameter_name, p in module.named_parameters():
            devices[str(p.device)] = devices.get(str(p.device), 0) + p.numel()
            if p.is_floating_point():
                dtypes[str(p.dtype)] = dtypes.get(str(p.dtype), 0) + p.numel()
                wanted = expected
                if (
                    name == "talker"
                    and fp32_predictor
                    and parameter_name.startswith("talker.code_predictor.")
                    and id(p) not in embeddings
                ):
                    wanted = expected_codec_dtype
                if p.dtype != wanted:
                    precision_errors.append(parameter_name)
        if set(devices) != {"cuda:0"} or precision_errors:
            raise RuntimeError(
                f"{name} residency/precision differs: {devices}, {dtypes}"
            )
        mapping = getattr(module, "hf_device_map", None)
        if mapping and not {str(v) for v in mapping.values()} <= {"0", "cuda:0"}:
            raise RuntimeError("Hidden CPU/disk offload")
        result[name] = {"devices": devices, "dtypes": dtypes}
    return result
