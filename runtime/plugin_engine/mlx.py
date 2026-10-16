"""Adapt the existing all-model loader to the measured speech-output contract.

The TTS model is loaded once by MLXModels. Generation and token-limit accounting
come from MLXTTSBackend, the same backend used by the standalone output service.
"""

from runtime.speech_output.mlx_backend import MLXTTSBackend
from runtime.voice_core.mlx_live import MLXModels


class ResidentMLXModels(MLXModels):
    max_tokens = MLXTTSBackend.max_tokens
    tts_stream = MLXTTSBackend.tts_stream
    tts_next = staticmethod(MLXTTSBackend.tts_next)

    @property
    def model(self):
        return self.tts

    def __init__(self, *args, voice_catalog=None, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.mx.metal.is_available() or self.mx.default_device() != self.mx.gpu:
            raise RuntimeError("native MLX GPU required; no implicit CPU fallback")
        self.identity["tts_service"] = {
            "backend": "mlx-metal",
            "sample_rate": 24000,
            "max_tokens_per_segment": self.max_tokens,
        }
        from .options import MLXOptions
        from .voices import ReferenceVoices

        self.reference_voices = None
        if voice_catalog is not None:
            if self.tts.speaker_encoder is None:
                raise RuntimeError(
                    "reference voices require the bound model speaker encoder"
                )
            self.reference_voices = ReferenceVoices(voice_catalog, self.mx)
            self.identity["tts_service"]["reference_voices"] = (
                self.reference_voices.identity
            )

        paths = self.identity["models"]
        self.operator_catalog = MLXOptions.from_snapshots(
            paths["stt"]["snapshot"],
            paths["tts"]["snapshot"],
            reference_voices=self.reference_voices,
        )

    def bind_operator_settings(self, values):
        return self.operator_catalog.bind(self, values)
