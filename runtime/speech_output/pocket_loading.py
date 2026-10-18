def load_bound_model(config):
    from pocket_tts.models.tts_model import (
        DEFAULT_EOS_THRESHOLD,
        DEFAULT_NOISE_CLAMP,
        DEFAULT_SAMPLER_DECODE_STEPS,
        TTSModel,
    )
    from pocket_tts.utils.config import Config

    validated = Config(**config)
    return TTSModel._from_pydantic_config_with_weights(
        validated,
        validated.default_temperature,
        DEFAULT_SAMPLER_DECODE_STEPS,
        DEFAULT_NOISE_CLAMP,
        DEFAULT_EOS_THRESHOLD,
        origin=None,
    )
