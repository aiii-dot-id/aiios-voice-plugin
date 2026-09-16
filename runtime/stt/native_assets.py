"""Runtime-owned bindings for the checkpoint-aligned Nemotron ONNX recognizer.

Installed loading consumes only ModelAssets. No research modules, test signals,
model discovery, downloads or Python paths from model data. The explicit lab
loader retains the old frozen proof layout; it is not an installed override.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from runtime.model_assets import checked, file_hashes, read_json

CHECKPOINT_SHA = "9eebdd6590289cb3030f310858f3df93256600a800a3e8200c5993d5f967e174"
CHECKPOINT_BYTES = 2552062944
MODEL_MANIFEST_SHA = "79136db6634bf183f19b903424a8a9e5456633223d8e75f580b7798c3d90f122"
QUALIFICATION_MANIFEST_SHA = (
    "8594731b657532ec5344d2625ad5967b05972c46a7717b8d75b925d2acc8dc11"
)
MEL_SHA = "f5608e00763a20a191022fbb00a43346db5f1ec06828a97a6f4157f7d02b76c8"
MEL_ENCODING = "ieee754-f32-le-uint32"
PINNED_FILES = {
    "encoder.onnx": (
        42281004,
        "8b136db725b3c10b68a106482f0e83bf6a1136570959fe437cb8632222f7c3d7",
    ),
    "decoder.onnx": (
        59764944,
        "f9c59ee6fa130bc2ba349dbcbba7c74a4a960a98d4196295ba4e8e5d6bde6b68",
    ),
    "joiner.onnx": (
        37824291,
        "a6bd74c0a31cbde0da0368c1e29d171752108c7ad1231630e079a7d97ceee6f0",
    ),
    "tokens.txt": (
        131440,
        "729cc103155bafa785f9cd45746cd41cabe97eab7182fc04d594129587958f8a",
    ),
    "weight-view.json": (
        197045,
        "b5373e14f6faeee3a405f8b13e5742ed5156b12bf294f85d61e9aada5bfd9a76",
    ),
    "model.safetensors": (CHECKPOINT_BYTES, CHECKPOINT_SHA),
}

# Exact CPU-BASIC export, qualified with DirectML ENABLE_ALL on the Windows
# target. A catalog must declare BOTH files; discovery never selects a cache.
# The source checkpoint and all 640 original tensor checks remain mandatory.
PRECOMPUTED_FILES = {
    "encoder.optimized.onnx": (
        746856,
        "37a1bcb19e92dfacbffd5f18e2948d30412d88a277e9eb943399654f0069af5b",
    ),
    "weights.bin": (
        2495361024,
        "cacfba092fa4b51d8adf6bf54c8295e11344e91ed124ac503e7bc3e2afc91808",
    ),
}


def verify_weights(model, checkpoint):
    """Read back all 640 encoder spans without importing a training framework."""
    bound, _ = read_json(checked(model, "weight-view.json"))
    if (bound["checkpoint_sha256"], bound["checkpoint_bytes"]) != (
        CHECKPOINT_SHA,
        CHECKPOINT_BYTES,
    ) or file_hashes(checkpoint, CHECKPOINT_BYTES)[0] != CHECKPOINT_SHA:
        raise ValueError("Installed checkpoint differs")
    records = bound["initializers"]
    if not isinstance(records, list) or len(records) != 640:
        raise ValueError("Incomplete tensor census")
    names, references = set(), set()
    for row in records:
        offset, size = row["offset"], row["bytes"]
        if (
            not isinstance(row["initializer"], str)
            or not row["initializer"]
            or row["initializer"] in names
            or not isinstance(row["reference"], str)
            or not row["reference"]
            or row["reference"] in references
            or type(offset) is not int
            or type(size) is not int
            or offset < 8
            or size <= 0
            or offset + size > CHECKPOINT_BYTES
            or not isinstance(row["reference_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", row["reference_sha256"])
        ):
            raise ValueError("Invalid or duplicate tensor extent/identity")
        names.add(row["initializer"])
        references.add(row["reference"])
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    with os.fdopen(os.open(checkpoint, flags), "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size != CHECKPOINT_BYTES:
            raise ValueError("Checkpoint file changed before tensor readback")
        for row in records:
            handle.seek(row["offset"])
            remaining, digest = row["bytes"], hashlib.sha256()
            while remaining:
                data = handle.read(min(8 * 1024 * 1024, remaining))
                if not data:
                    raise ValueError("Truncated tensor")
                digest.update(data)
                remaining -= len(data)
            if digest.hexdigest() != row["reference_sha256"]:
                raise ValueError("Installed tensor differs: " + row["reference"])
        after = os.fstat(handle.fileno())
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ValueError("Checkpoint changed during tensor readback")
    return len(records)


def filters(path):
    """Bit-exact portable coefficients; bounded JSON, not a feature/test archive."""
    body, _ = read_json(path)
    if (
        set(body) != {"encoding", "shape", "values"}
        or body["encoding"] != MEL_ENCODING
        or body["shape"] != [128, 257]
    ):
        raise ValueError("Native mel coefficient format differs")
    values = body["values"]
    if (
        not isinstance(values, list)
        or len(values) != 128 * 257
        or any(type(v) is not int or not 0 <= v <= 0xFFFFFFFF for v in values)
    ):
        raise ValueError("Native mel coefficient count or bit pattern invalid")
    mel = np.asarray(values, dtype="<u4").view("<f4").reshape(128, 257)
    if (
        not np.isfinite(mel).all()
        or np.any(mel < 0)
        or hashlib.sha256(mel.tobytes()).hexdigest() != MEL_SHA
    ):
        raise ValueError("Native mel coefficients differ from the exact frontend")
    mel.flags.writeable = False
    return mel


@dataclass(frozen=True)
class NativeAssets:
    model: Path
    mel: np.ndarray
    identity: dict
    encoder: Path | None = None

    @property
    def encoder_path(self):
        return self.encoder if self.encoder is not None else self.model / "encoder.onnx"


def load_installed(assets):
    """Existing signed-runtime catalog is the authority; no second manifest store."""
    group = assets.groups["stt-native"]
    original = {*PINNED_FILES, "mel-filters.json"}
    cached = set(group["files"]) == original | PRECOMPUTED_FILES.keys()
    if set(group["files"]) != original and not cached:
        raise ValueError("Native installed model census differs")
    if cached and assets.backend != "windows-pocket":
        raise ValueError("Precomputed native encoder requires the Windows backend")
    pins = {**PINNED_FILES, **(PRECOMPUTED_FILES if cached else {})}
    for name, (size, digest) in pins.items():
        if group["files"][name] != {"bytes": size, "sha256": digest}:
            raise ValueError("Native graph/checkpoint binding differs: " + name)
    model = assets.snapshot("stt-native")
    count = verify_weights(model, checked(model, "model.safetensors"))
    return NativeAssets(
        model,
        filters(checked(model, "mel-filters.json")),
        {
            "asset_layout": "installed-model-assets",
            "asset_manifest_sha256": assets.manifest_sha("stt-native"),
            "checkpoint_sha256": CHECKPOINT_SHA,
            "model_manifest_sha256": MODEL_MANIFEST_SHA,
            "verified_encoder_tensors": count,
            "frontend_sha256": MEL_SHA,
            **(
                {
                    "encoder_artifact": "encoder.optimized.onnx",
                    "encoder_artifact_sha256": PRECOMPUTED_FILES[
                        "encoder.optimized.onnx"
                    ][1],
                    "encoder_external_data_sha256": PRECOMPUTED_FILES["weights.bin"][1],
                    "encoder_derivation": "CPU BASIC before provider partition; DirectML ENABLE_ALL reload",
                    "tensor_verification_scope": "640 source checkpoint spans; derived graph/data whole-file SHA-256",
                }
                if cached
                else {}
            ),
        },
        checked(model, "encoder.optimized.onnx") if cached else None,
    )


def load_qualification(root):
    """Old explicit, byte-pinned lab entry retained; never selected by discovery."""
    manifest, raw = read_json(checked(root, "input-manifest.json"))
    digest = hashlib.sha256(raw).hexdigest()
    if digest != QUALIFICATION_MANIFEST_SHA:
        raise ValueError("Native candidate differs from its frozen input manifest")
    for name, row in manifest.items():
        if file_hashes(checked(root, name), row["bytes"])[0] != row["sha256"]:
            raise ValueError("Source/model/input changed: " + name)
    model = checked(root, "model", directory=True)
    count = verify_weights(model, checked(model, "model.safetensors"))
    with np.load(checked(root, "inputs/features.npz"), allow_pickle=False) as data:
        mel = data["mel_filters"].copy()
    if (
        mel.shape != (128, 257)
        or mel.dtype != np.float32
        or hashlib.sha256(mel.astype("<f4").tobytes()).hexdigest() != MEL_SHA
    ):
        raise ValueError("Qualification frontend differs")
    return NativeAssets(
        model,
        mel,
        {
            "input_manifest_sha256": digest,
            "checkpoint_sha256": CHECKPOINT_SHA,
            "verified_encoder_tensors": count,
            "model_manifest_sha256": MODEL_MANIFEST_SHA,
        },
    )
