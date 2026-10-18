from pathlib import Path


def assign_exact_state(model, state):
    expected = model.state_dict()
    if set(state) != set(expected):
        raise ValueError("checkpoint keys differ from the exact model")
    for module in model.modules():
        if any(
            module._buffers.get(n) is not None
            for n in module._non_persistent_buffers_set
        ):
            raise ValueError("uncheckpointed buffer cannot be materialized")
    materialized = {}
    for name, before in expected.items():
        value = state[name]
        if value.shape != before.shape or value.device.type != "cpu":
            raise ValueError("checkpoint shape/device differs: " + name)
        materialized[name] = value.to(dtype=before.dtype, device="cpu")
    model.load_state_dict(materialized, strict=True, assign=True)
    if any(t.device.type != "cpu" for t in (*model.parameters(), *model.buffers())):
        raise ValueError("model contains an unmaterialized tensor")


def load_bound_model_meta(config):
    import safetensors.torch
    import torch
    from pocket_tts.models.mimi import build_mimi
    from pocket_tts.models.tts_model import (
        DEFAULT_EOS_THRESHOLD,
        DEFAULT_NOISE_CLAMP,
        DEFAULT_SAMPLER_DECODE_STEPS,
        TTSModel,
        stamp_state_names,
    )
    from pocket_tts.utils.config import Config

    validated = Config(**config)
    if (
        not validated.weights_path
        or not Path(validated.weights_path).is_absolute()
        or validated.flow_lm.weights_path is not None
        or validated.mimi.weights_path is not None
        or validated.weights_path_without_voice_cloning is not None
    ):
        raise ValueError("one verified local bundle required; no alternate loader")
    with torch.device("meta"):
        model = TTSModel._from_pydantic_config(
            validated,
            validated.default_temperature,
            DEFAULT_SAMPLER_DECODE_STEPS,
            DEFAULT_NOISE_CLAMP,
            DEFAULT_EOS_THRESHOLD,
            origin=None,
        )
        model.flow_lm.speaker_proj_weight = torch.nn.Parameter(
            torch.zeros(
                (
                    validated.flow_lm.transformer.d_model,
                    validated.mimi.inner_dim or validated.mimi.seanet.dimension,
                ),
                dtype=torch.float32,
            )
        )
        model.mimi = build_mimi(validated.mimi)
    state = safetensors.torch.load_file(validated.weights_path, device="cpu")
    assign_exact_state(model, state)
    model.mimi.eval()
    stamp_state_names(model)
    return model
