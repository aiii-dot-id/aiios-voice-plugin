"""Explicit immutable-profile selection; absent means unchanged float execution.

No CLI/environment override, checkpoint rewrite, download, or fallback. The
carrier verifies the entire runtime before importing this module. Selection is
specific to the measured Windows CPU profile, not a new host or public SDK API.
"""

import hashlib
import json

from runtime.speech_output.pocket_quantization import build, packed_identity, targets

PROFILE = {
    "mode": "flowlm-attention-ffn-int8",
    "engine": "fbgemm",
    "torch_version": "2.8.0+cpu",
    "threads": 1,
    "packed_sha256": "01ed9e2adcc92a0803559582e55e0181786bbbfe4f889c838744715a027a52e4",
}


def selection(runtime_root, profile=None):
    if runtime_root is None:
        return None
    if profile is None:
        profile = json.loads((runtime_root / "voice-runtime.json").read_text())
    if "pocket_execution" not in profile:
        return None
    if (
        (profile.get("platform"), profile.get("arch"), profile.get("backend"))
        != (
            "windows",
            "amd64",
            "windows-pocket",
        )
        or profile["pocket_execution"] != PROFILE
        or type(profile["pocket_execution"]["threads"]) is not int
    ):
        raise ValueError("unsupported or unbound Pocket execution selection")
    return dict(PROFILE)


def tensor_identity(values):
    import torch

    result = {}
    for name, value in values.items():
        if value.device.type != "cpu":
            raise ValueError("Pocket execution requires materialized CPU tensors")
        raw = (
            value.detach().contiguous().reshape(-1).view(torch.uint8).numpy().tobytes()
        )
        result[name] = {
            "shape": list(value.shape),
            "dtype": str(value.dtype),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    return result


def apply(model, selected):
    if selected is None:
        return None
    import torch

    if (
        selected != PROFILE
        or type(selected["threads"]) is not int
        or str(torch.__version__) != selected["torch_version"]
        or torch.get_num_threads() != 1
    ):
        raise ValueError("Pocket execution engine/version/thread binding differs")
    original = model.flow_lm
    names = targets(original)
    if len(names) != 24:
        raise ValueError("Pocket execution requires the measured 24-module graph")
    before = tensor_identity(model.state_dict())
    candidate = build(original, engine=selected["engine"])
    packed = packed_identity(candidate)
    digest = hashlib.sha256(json.dumps(packed, sort_keys=True).encode()).hexdigest()
    if digest != selected["packed_sha256"]:
        raise ValueError("Pocket packed weights differ from measured candidate")
    keep = lambda name: not any(name.startswith(n + ".") for n in names)
    expected = tensor_identity(
        {n: v for n, v in original.state_dict().items() if keep(n)}
    )
    observed = tensor_identity(
        {n: v for n, v in candidate.state_dict().items() if keep(n)}
    )
    if expected != observed or tensor_identity(model.state_dict()) != before:
        raise ValueError("Pocket original or non-target tensors changed")
    # Publish only after every check; a refused selection cannot leave a partly
    # changed resident. This process owns the derivative, not another session.
    model.flow_lm = candidate
    return {
        **selected,
        "modules": len(packed),
        "original_tensors": len(before),
        "original_state_sha256": hashlib.sha256(
            json.dumps(before, sort_keys=True).encode()
        ).hexdigest(),
        "non_target_tensors_unchanged": True,
        "qualification": "candidate; no human-quality or release claim",
    }
