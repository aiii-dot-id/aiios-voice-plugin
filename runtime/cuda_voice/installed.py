"""Resolve the unchanged CUDA TTS checkpoint and reference without a checkout.

The carrier verifies all runtime code/resources before calling this consumer.
The model catalog remains runtime-owned; model data never supplies imports.
"""

import hashlib
import json

TTS_MANIFEST_SHA = "ae5f6a89c818172226f0a1f9937cb1098786d2a86c3a09a319e84fb91286305c"


def tts_inputs(runtime_root, assets):
    raw = (runtime_root / "resources/cuda/reference-manifest.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != TTS_MANIFEST_SHA:
        raise ValueError("packaged CUDA reference contract differs")
    manifest = json.loads(raw)
    model = manifest["model"]
    projection = assets.manifests["tts"]
    if (
        projection["source"]
        != {"repo_id": model["repository"], "revision": model["revision"]}
        or projection["files"] != model["files"]
        or projection["reference_contract_sha256"] != TTS_MANIFEST_SHA
    ):
        raise ValueError("installed CUDA TTS projection differs from original")
    snapshot = assets.snapshot("tts")
    reference = json.loads(
        (runtime_root / "resources/cuda/reference-fixture.json").read_bytes()
    )
    # Public, development-only conditioning audio travels as a signed runtime
    # resource, not an invented URL for a locally converted WAV in ModelDecl.
    audio = runtime_root / "resources/cuda/reference.wav"
    if hashlib.sha256(audio.read_bytes()).hexdigest() != reference["audio_sha256"]:
        raise ValueError("packaged public voice-conditioning fixture differs")
    return model, snapshot, dict(reference, audio_path=str(audio))
