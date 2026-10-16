"""Isolated use of pinned Pocket's selective INT8 implementation.

Only the FlowLM attention/FFN linears change. The baseline object is never
modified; the caller owns both models and must restore the original reference.
No runtime defaults, checkpoint files or backend fallback are introduced.
"""

import copy
import hashlib


def targets(flow):
    return [
        f"transformer.layers.{i}.{suffix}"
        for i in range(len(flow.transformer.layers))
        for suffix in ("self_attn.in_proj", "self_attn.out_proj", "linear1", "linear2")
    ]


def packed_identity(flow):
    import torch
    from torch.ao.nn.quantized.dynamic import Linear

    names = targets(flow)
    actual = {n for n, m in flow.named_modules() if isinstance(m, Linear)}
    if not names or actual != set(names):
        raise ValueError("INT8 coverage differs from attention and FFN only")
    result = {}
    for name in names:
        weight = flow.get_submodule(name).weight()
        if weight.dtype != torch.qint8 or weight.qscheme() != torch.per_tensor_affine:
            raise ValueError("unexpected packed weight format")
        raw = weight.int_repr().contiguous().numpy().tobytes()
        result[name] = {
            "shape": list(weight.shape),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "scale": weight.q_scale(),
            "zero_point": weight.q_zero_point(),
        }
    return result


def build(flow, *, engine):
    import torch
    from pocket_tts.quantization import _apply_torch_ao

    if engine not in torch.backends.quantized.supported_engines or engine == "none":
        raise ValueError("requested INT8 engine unavailable; fallback refused")
    if any(type(flow.get_submodule(n)) is not torch.nn.Linear for n in targets(flow)):
        raise ValueError("expected original float Linear modules")
    previous = torch.backends.quantized.engine
    if previous not in torch.backends.quantized.supported_engines:
        raise ValueError("caller must initialize a restorable quantization engine")
    candidate = copy.deepcopy(flow)
    try:
        torch.backends.quantized.engine = engine
        _apply_torch_ao(candidate, {"attention", "ffn"})
        if torch.backends.quantized.engine != engine:
            raise ValueError("upstream changed the requested INT8 engine")
        packed_identity(candidate)
        return candidate
    finally:
        torch.backends.quantized.engine = previous
