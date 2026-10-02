"""UID consumes the existing signed-runtime catalog and enrollment store.

No discovery, downloads, enrollment or public SDK wire is introduced. The
composition root supplies explicit enablement by constructing SpeakerTools with
ModelAssets; heavy verification remains on that tool's bounded inference worker.
"""

from pathlib import Path

from runtime.model_assets import checked, read_json

from .identity import Policy, SpeakerIdentityError

PROVIDERS = {"mlx": "mlx", "cuda": "cuda", "windows-pocket": "directml"}


def configuration(config, assets):
    if set(config) != {"database", "context", "provider"}:
        raise SpeakerIdentityError("installed UID refuses research-path overrides")
    policy = policy_for({k: config[k] for k in ("context", "provider")}, assets)
    database = Path(config["database"])
    if (
        not database.is_absolute()
        or database.resolve().is_relative_to(assets.root)
        or database.resolve().is_relative_to(assets.catalog.parent)
    ):
        raise SpeakerIdentityError("enrollment must be outside runtime and model data")
    return policy, database


def policy_for(config, assets):
    """Common catalog policy for file-backed and host-snapshot composition."""
    if set(config) != {"context", "provider"}:
        raise SpeakerIdentityError("installed UID refuses research-path overrides")
    if (
        assets.backend not in PROVIDERS
        or config["context"] != "full_utterance"
        or config["provider"] != PROVIDERS.get(assets.backend)
    ):
        raise SpeakerIdentityError("installed UID context/backend differs")
    if "uid" not in assets.groups:
        raise SpeakerIdentityError("installed UID model group missing")
    raw, _ = read_json(checked(assets.catalog.parent, "uid-policy.json"))
    policy = Policy(**raw)
    if policy.minimum_enrollment_samples < 3:
        raise SpeakerIdentityError(
            "installed UID requires three distinct enrollment recordings"
        )
    return policy


def model_config(config, assets):
    """Called inside SpeakerTools' actual worker, never the control owner."""
    root = assets.snapshot("uid")
    result = {**config, "source_model": str(checked(root, "model.onnx"))}
    if config["provider"] == "mlx":
        manifest_path = checked(root, "mlx/manifest.json")
        manifest, _ = read_json(manifest_path)
        if manifest.get("tensors_format") != "safetensors":
            raise SpeakerIdentityError("installed UID requires data-only safetensors")
        result.update(
            lowering=str(checked(root, "mlx", directory=True)),
            lowering_manifest_sha256=assets.groups["uid"]["files"]["mlx/manifest.json"][
                "sha256"
            ],
        )
    return result
