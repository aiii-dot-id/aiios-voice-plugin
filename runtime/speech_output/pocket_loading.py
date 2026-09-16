"""The pinned Pocket file loader's exact factory, without a temporary YAML file.

Call only after the owning adapter verifies Pocket source and asset bytes. This
does not add a downloader, fallback, alternate config schema or model instance.
"""


def load_bound_model(config):
    from pocket_tts.models.tts_model import (
        DEFAULT_EOS_THRESHOLD,
        DEFAULT_NOISE_CLAMP,
        DEFAULT_SAMPLER_DECODE_STEPS,
        TTSModel,
    )
    from pocket_tts.utils.config import Config

    # Equivalent to load_model(config=<bound YAML>) with all public defaults:
    # strict upstream validation, recommended temperature, no quantization or
    # training-checkpoint override. All asset paths are already local/absolute.
    # origin is used only for shorthand named voices; we always supply the
    # verified preset embedding explicitly, never that implicit lookup route.
    validated = Config(**config)
    return TTSModel._from_pydantic_config_with_weights(
        validated,
        validated.default_temperature,
        DEFAULT_SAMPLER_DECODE_STEPS,
        DEFAULT_NOISE_CLAMP,
        DEFAULT_EOS_THRESHOLD,
        origin=None,
    )
