"""Model-derived MLX choices and immutable, per-session execution settings.

Only verified snapshots may be supplied. Model-card support and executable
prompt IDs must agree; a reserved prompt ID is not a supported language. This
module owns no persistence, downloader, audio device or public SDK operation.
"""

from __future__ import annotations

import copy
import math
from pathlib import Path
from types import MappingProxyType

from runtime.model_assets import checked, read_json
from runtime.speech_output.mlx_backend import MLXTTSBackend
from runtime.voice_core.preview_stt import PreviewSTT

LOCALE_LABELS = {
    "ar-AR": "Arabic",
    "auto": "Automatic",
    "bg-BG": "Bulgarian",
    "cs-CZ": "Czech",
    "da-DK": "Danish",
    "de-DE": "German (Germany)",
    "el-GR": "Greek",
    "en-GB": "English (United Kingdom)",
    "en-US": "English (United States)",
    "es-ES": "Spanish (Spain)",
    "es-US": "Spanish (United States)",
    "et-EE": "Estonian",
    "fi-FI": "Finnish",
    "fr-CA": "French (Canada)",
    "fr-FR": "French (France)",
    "he-IL": "Hebrew",
    "hi-IN": "Hindi",
    "hr-HR": "Croatian",
    "hu-HU": "Hungarian",
    "it-IT": "Italian",
    "ja-JP": "Japanese",
    "ko-KR": "Korean",
    "lt-LT": "Lithuanian",
    "lv-LV": "Latvian",
    "mt-MT": "Maltese",
    "nl-NL": "Dutch",
    "no-NO": "Norwegian",
    "pl-PL": "Polish",
    "pt-BR": "Portuguese (Brazil)",
    "pt-PT": "Portuguese (Portugal)",
    "ro-RO": "Romanian",
    "ru-RU": "Russian",
    "sk-SK": "Slovak",
    "sl-SI": "Slovenian",
    "sv-SE": "Swedish",
    "th-TH": "Thai",
    "tr-TR": "Turkish",
    "uk-UA": "Ukrainian",
    "vi-VN": "Vietnamese",
    "zh-CN": "Chinese (Mainland China)",
    "zh-TW": "Chinese (Taiwan)",
}


def recognition_choices(config, language_codes):
    if (
        not isinstance(language_codes, list)
        or not language_codes
        or any(not isinstance(s, str) or not s for s in language_codes)
        or len(set(language_codes)) != len(language_codes)
    ):
        raise ValueError("model card must declare distinct language codes")
    prompt = config["prompt"]
    choices, covered = {}, set()
    for key, index in prompt["prompt_dictionary"].items():
        if (
            not isinstance(key, str)
            or not key
            or type(index) is not int
            or not 0 <= index < prompt["num_prompts"]
        ):
            raise ValueError("invalid recognizer prompt binding")
        base = key.split("-")[0]
        if key == "auto" or ("-" in key and base in language_codes):
            # This bound configuration lists preferred locale keys before their
            # equivalent aliases. Keep separate indices for separate locales.
            choices.setdefault(index, key)
            covered.add(base)
    if set(language_codes) - covered:
        raise ValueError("model-card language has no executable locale prompt")
    return tuple(sorted(choices.values()))


class MLXOptions:
    """One source for declaration, validation and the model's actual arguments."""

    def __init__(self, stt_config, stt_languages, tts_config, *, reference_voices=None):
        self.stt_languages = recognition_choices(stt_config, stt_languages)
        talker = tts_config["talker_config"]
        if tts_config["tts_model_type"] != "base":
            raise ValueError("this adapter binds the Qwen Base generation path")
        languages = talker["codec_language_id"]
        voices = talker.get("spk_id") or {}
        if (
            not isinstance(languages, dict)
            or not languages
            or not isinstance(voices, dict)
            or any(not isinstance(s, str) or not s for s in (*languages, *voices))
        ):
            raise ValueError("invalid synthesizer choices")
        self.tts_languages = tuple(sorted({"auto", *languages}))
        self.reference_voices = reference_voices
        if reference_voices is not None and voices:
            raise ValueError("reference and preset voice catalogs cannot be mixed")
        self.voices = tuple(
            sorted(reference_voices.labels if reference_voices is not None else voices)
        )
        if "en-US" not in self.stt_languages or "english" not in self.tts_languages:
            raise ValueError("checkpoint default languages are unavailable")
        vocab = talker["vocab_size"]
        if type(vocab) is not int or not 50 <= vocab <= 100000:
            raise ValueError("invalid synthesis vocabulary bound")
        self._decls = [
            self._enum(
                "stt_language", "Recognition language", self.stt_languages, "en-US"
            ),
            self._enum(
                "tts_language", "Speaking language", self.tts_languages, "english"
            ),
            self._number("tts_temperature", "Voice variation", 0.9, 0, 2),
            self._number("tts_top_k", "Sampling top-k", 50, 0, vocab),
            self._number("tts_top_p", "Sampling top-p", 1.0, 0.01, 1),
            self._number("tts_repetition_penalty", "Repetition penalty", 1.05, 1, 2),
            self._number("tts_seed", "Synthesis seed", 17, 0, 2147483647),
            {
                "key": "turn_pause_ms",
                "type": "integer",
                "title": "Pause before replying (ms)",
                "default": 1200,
                "minimum": 320,
                "maximum": 5000,
                "description": "Minimum silence before submitting an utterance. Brief pauses keep it open. Rounded up to the next 32 ms audio block; semantic incompleteness can add up to 1152 ms. Applies next session; does not delay interruption.",
            },
        ]
        if reference_voices is not None:
            decl = self._enum(
                "tts_voice", "Speaking voice", self.voices, reference_voices.default
            )
            decl["labels"] = dict(reference_voices.labels)
            decl["description"] = (
                "Fixed reference voice for every reply segment. Applies next session. Separate from enrolled speaker identity."
            )
            self._decls.append(decl)
        elif self.voices:
            # Do not select an arbitrary speaker. "default" explicitly means
            # the Base model's existing unconditioned path, not a named voice.
            if "default" in self.voices:
                raise ValueError("speaker ID conflicts with unconditioned default")
            self._decls.append(
                self._enum(
                    "tts_voice", "Speaking voice", ("default", *self.voices), "default"
                )
            )
        self._integers = frozenset({"tts_top_k", "tts_seed", "turn_pause_ms"})
        for declaration in self._decls:
            if declaration["key"] in self._integers:
                declaration["type"] = "integer"

    @classmethod
    def from_snapshots(cls, stt, tts, *, reference_voices=None):
        # The loader verified and pinned these files before this metadata-only
        # read. No inference or filesystem discovery is performed here.
        import yaml

        stt, tts = Path(stt), Path(tts)
        config, _ = read_json(checked(stt, "config.json"))
        tts_config, _ = read_json(checked(tts, "config.json"))
        card = checked(stt, "README.md")
        if card.stat().st_size > 65536:
            raise ValueError("model card exceeds metadata bound")
        text = card.read_text(encoding="utf-8")
        parts = text.split("---", 2)
        if len(parts) != 3 or parts[0].strip():
            raise ValueError("model card lacks bounded frontmatter")
        metadata = yaml.safe_load(parts[1])
        return cls(
            config, metadata["language"], tts_config, reference_voices=reference_voices
        )

    @staticmethod
    def _enum(key, title, values, default):
        return {
            "key": key,
            "type": "enum",
            "title": title,
            "values": list(values),
            "labels": {
                value: LOCALE_LABELS.get(value, value.capitalize()) for value in values
            },
            "default": default,
            "description": "Applies to the next session. Model-supported; not a per-language quality certification.",
        }

    @staticmethod
    def _number(key, title, default, minimum, maximum):
        return {
            "key": key,
            "type": "number",
            "title": title,
            "default": default,
            "minimum": minimum,
            "maximum": maximum,
            "description": "Advanced. Applies to the next session; changes may affect speech quality. Top-k and seed require whole numbers.",
        }

    def declarations(self):
        return copy.deepcopy(self._decls)

    def inventory(self):
        return {
            "stt_languages": list(self.stt_languages),
            "tts_languages": list(self.tts_languages),
            "named_voices": list(self.voices),
            "default_voice": self.reference_voices.default
            if self.reference_voices is not None
            else "unconditioned_base",
            "unsupported_controls": {
                "speed": "backend does not implement speed",
                "pitch": "backend does not implement pitch",
                "style": "Base adapter has no style-instruction path",
            },
            "settings": self.declarations(),
            "evidence": "model_support_not_quality_qualification",
        }

    def validate(self, requested):
        if type(requested) is not dict:
            raise ValueError("operator settings must be an object")
        unknown = set(requested) - {d["key"] for d in self._decls}
        if unknown:
            raise ValueError(
                "unsupported operator setting: " + repr(sorted(unknown, key=str))
            )
        values = {}
        for decl in self._decls:
            key = decl["key"]
            value = requested.get(key, decl["default"])
            if decl["type"] == "enum":
                valid = type(value) is str and value in decl["values"]
            else:
                valid = (
                    type(value) in (float, int)
                    and decl["minimum"] <= value <= decl["maximum"]
                    and math.isfinite(value)
                )
                if valid and key in self._integers:
                    valid = int(value) == value
                    if valid:
                        value = int(value)
            if not valid:
                raise ValueError("unsupported value for operator setting " + key)
            values[key] = value
        return MappingProxyType(values)

    def bind(self, models, requested):
        return SessionMLXModels(models, self.validate(requested), self.reference_voices)


class SessionMLXModels:
    """Only the session view changes; resident weights and other sessions do not."""

    tts_stream = MLXTTSBackend.tts_stream
    tts_next = staticmethod(MLXTTSBackend.tts_next)

    def __init__(self, models, values, reference_voices=None):
        self._models = models
        self.operator_settings = MappingProxyType(dict(values))
        self.tts_seed = values["tts_seed"]
        self.turn_pause_ms = values["turn_pause_ms"]
        generation = {
            "lang_code": values["tts_language"],
            "temperature": values["tts_temperature"],
            "top_k": values["tts_top_k"],
            "top_p": values["tts_top_p"],
            "repetition_penalty": values["tts_repetition_penalty"],
        }
        if reference_voices is not None:
            generation.update(reference_voices.generation(values["tts_voice"]))
        elif values.get("tts_voice", "default") != "default":
            generation["voice"] = values["tts_voice"]
        self.tts_generation_options = MappingProxyType(generation)

    def __getattr__(self, key):
        return getattr(self._models, key)

    @property
    def model(self):
        return self._models.tts

    def stt_stream(self):
        return PreviewSTT(
            self._models.stt,
            self._models.stt_right_context,
            language=self.operator_settings["stt_language"],
        )
