"""Transcript providers for native turn-control experiments."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import numpy as np


class CausalWordTranscriptProvider:
    """Expose sealed oracle words only after their annotated end boundary."""

    def __init__(
        self,
        words: Sequence[dict[str, Any]],
        *,
        sample_rate: int = 16_000,
    ) -> None:
        if (
            isinstance(sample_rate, bool)
            or not isinstance(sample_rate, int)
            or sample_rate <= 0
        ):
            raise ValueError("oracle transcript sample rate must be a positive integer")
        if not words:
            raise ValueError("oracle transcript requires one or more timed words")
        prepared = []
        previous_start = -1
        previous_end = -1
        for index, row in enumerate(words):
            text = row.get("text") if isinstance(row, dict) else None
            start = row.get("start_seconds") if isinstance(row, dict) else None
            end = row.get("end_seconds") if isinstance(row, dict) else None
            if (
                not isinstance(text, str)
                or not text.strip()
                or isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, (int, float))
                or not isinstance(end, (int, float))
            ):
                raise ValueError(f"oracle transcript word {index} is malformed")
            start_sample = round(float(start) * sample_rate)
            end_sample = round(float(end) * sample_rate)
            if (
                start_sample < 0
                or end_sample <= start_sample
                or start_sample < previous_start
                or end_sample < previous_end
            ):
                raise ValueError(f"oracle transcript word {index} has invalid time")
            prepared.append((text.strip(), start_sample, end_sample))
            previous_start = start_sample
            previous_end = end_sample
        self.sample_rate = sample_rate
        self._words = tuple(prepared)
        self._received_samples = 0
        self.raw_text = ""
        self.observations = 0
        self.finalized = False

    def _refresh(self) -> None:
        self.raw_text = " ".join(
            text for text, _, end in self._words if end <= self._received_samples
        )

    def observe(
        self, samples: np.ndarray, sample_rate: int, *, final: bool = False
    ) -> None:
        if self.finalized:
            raise RuntimeError("cannot observe audio after transcript finalization")
        if sample_rate != self.sample_rate:
            raise ValueError(
                f"oracle transcript requires {self.sample_rate} Hz, got {sample_rate}"
            )
        samples = np.asarray(samples, dtype=np.float32)
        if samples.ndim != 1 or not np.isfinite(samples).all():
            raise ValueError("oracle transcript requires finite mono samples")
        self._received_samples += len(samples)
        self.observations += 1
        self._refresh()
        self.finalized = final

    def transcribe(self, samples: np.ndarray, sample_rate: int) -> str:
        samples = np.asarray(samples, dtype=np.float32)
        if sample_rate != self.sample_rate:
            raise ValueError(
                f"oracle transcript requires {self.sample_rate} Hz, got {sample_rate}"
            )
        if samples.ndim != 1 or not np.isfinite(samples).all():
            raise ValueError("oracle transcript requires finite mono samples")
        return self.raw_text

    @property
    def committed_text(self) -> str:
        return self.raw_text

    @property
    def conflicts(self) -> int:
        return 0


class StableTranscriptCommitter:
    """Expose only prefixes confirmed by two successive cumulative updates."""

    def __init__(
        self,
        *,
        encode: Callable[[str], list[int]],
        decode: Callable[[Sequence[int]], str],
    ) -> None:
        self._encode = encode
        self._decode = decode
        self._previous_raw: list[int] = []
        self._committed: list[int] = []
        self.conflicts = 0

    @staticmethod
    def _common_prefix(left: Sequence[int], right: Sequence[int]) -> list[int]:
        length = 0
        for left_id, right_id in zip(left, right, strict=False):
            if left_id != right_id:
                break
            length += 1
        return list(left[:length])

    def update(self, raw_text: str, *, final: bool = False) -> str:
        if not isinstance(raw_text, str):
            raise TypeError("raw transcript must be a string")
        raw = list(self._encode(raw_text.strip()))
        confirmed = raw if final else self._common_prefix(self._previous_raw, raw)
        if confirmed[: len(self._committed)] != self._committed:
            self.conflicts += 1
        elif len(confirmed) > len(self._committed):
            self._committed = confirmed
        self._previous_raw = raw
        return self.text

    @property
    def text(self) -> str:
        return self._decode(self._committed).strip() if self._committed else ""


class NemotronPushTranscriptProvider:
    """Persistent Nemotron stream with a monotonic confirmed-text boundary."""

    def __init__(
        self,
        model: Any,
        *,
        tokenizer: Any,
        language: str,
        attention_context: tuple[int, int] = (56, 6),
        max_audio_seconds: float = 120.0,
        stream_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not language.strip():
            raise ValueError("Nemotron language must not be empty")
        if (
            len(attention_context) != 2
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in attention_context
            )
        ):
            raise ValueError("attention_context must be two nonnegative integers")
        if max_audio_seconds <= 0:
            raise ValueError("max_audio_seconds must be positive")
        if stream_factory is None:
            from runtime.stt.nemotron_push import NemotronPushStream

            stream_factory = NemotronPushStream
        self.stream = stream_factory(
            model,
            language=language,
            att_context_size=attention_context,
            max_audio_seconds=max_audio_seconds,
        )
        self.committer = StableTranscriptCommitter(
            encode=lambda text: tokenizer.encode(text, add_special_tokens=False),
            decode=lambda ids: tokenizer.decode(list(ids)),
        )
        self.raw_text = ""
        self.observations = 0
        self.finalized = False

    def observe(
        self, samples: np.ndarray, sample_rate: int, *, final: bool = False
    ) -> None:
        if self.finalized:
            raise RuntimeError("cannot observe audio after transcript finalization")
        if sample_rate != int(self.stream.sample_rate):
            raise ValueError(
                f"Nemotron push provider requires {self.stream.sample_rate} Hz, "
                f"got {sample_rate}"
            )
        samples = np.asarray(samples, dtype=np.float32)
        if samples.ndim != 1 or not np.isfinite(samples).all():
            raise ValueError("Nemotron push provider requires finite mono samples")
        updates = self.stream.push_audio(samples, final=final)
        for update in updates:
            text = getattr(update.result, "text", None)
            if not isinstance(text, str):
                raise RuntimeError("Nemotron push update has no transcript text")
            self.raw_text = text.strip()
            self.committer.update(self.raw_text, final=bool(update.final))
            self.observations += 1
        if final:
            # A final push can legally produce no new decoder update. Commit the
            # most recent complete hypothesis only at the explicit stream end.
            self.committer.update(self.raw_text, final=True)
            self.finalized = True

    def transcribe(self, samples: np.ndarray, sample_rate: int) -> str:
        del samples
        if sample_rate != int(self.stream.sample_rate):
            raise ValueError(
                f"Nemotron push provider requires {self.stream.sample_rate} Hz, "
                f"got {sample_rate}"
            )
        return self.committer.text

    @property
    def committed_text(self) -> str:
        return self.committer.text

    @property
    def conflicts(self) -> int:
        return self.committer.conflicts


class NemotronWindowTranscriptProvider:
    """Transcribe the exact rolling window supplied by the turn controller.

    SoulX's public service reruns a cascade recognizer over a bounded recent
    buffer when its audio-token check detects speech.  This adapter preserves
    that boundary with the already-qualified local Nemotron model.  It is not
    the lower-latency persistent Nemotron push path and is named accordingly.
    """

    def __init__(
        self,
        model: Any,
        *,
        language: str,
        attention_context: tuple[int, int] = (56, 6),
        chunk_duration_seconds: float = 0.56,
    ) -> None:
        if not language.strip():
            raise ValueError("Nemotron language must not be empty")
        if (
            len(attention_context) != 2
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in attention_context
            )
        ):
            raise ValueError("attention_context must be two nonnegative integers")
        if chunk_duration_seconds <= 0:
            raise ValueError("chunk_duration_seconds must be positive")
        self.model = model
        self.language = language
        self.attention_context = list(attention_context)
        self.chunk_duration_seconds = chunk_duration_seconds
        self.calls = 0

    def transcribe(self, samples: np.ndarray, sample_rate: int) -> str:
        import mlx.core as mx

        samples = np.asarray(samples, dtype=np.float32)
        expected_rate = int(self.model.preprocessor_config.sample_rate)
        if sample_rate != expected_rate:
            raise ValueError(
                f"Nemotron provider requires {expected_rate} Hz, got {sample_rate}"
            )
        if samples.ndim != 1 or not np.isfinite(samples).all():
            raise ValueError("Nemotron provider requires finite mono samples")
        result = self.model.generate(
            mx.array(samples),
            language=self.language,
            att_context_size=self.attention_context,
            chunk_duration=self.chunk_duration_seconds,
            verbose=False,
        )
        text = getattr(result, "text", None)
        if not isinstance(text, str):
            raise RuntimeError("Nemotron did not return transcript text")
        self.calls += 1
        return text.strip()
