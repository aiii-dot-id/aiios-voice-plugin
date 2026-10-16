"""Stateful native streaming primitives for SoulX-Duplug.

The module keeps transport, transcript production, and the learned MLX core as
separate authorities.  It intentionally does not reproduce the upstream
service's CUDA-only ASR construction or process-global random reset padding.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Protocol, Sequence

import mlx.core as mx
import numpy as np


@dataclass(frozen=True)
class SoulXTokenIDs:
    task_duplex_predict: int = 151670
    punctuation_off: int = 151672
    audio_pad: int = 151673
    audio_eos: int = 151674
    audio_bos: int = 151675
    user_complete: int = 151676
    user_backchannel: int = 151677
    user_incomplete: int = 151678
    user_idle: int = 151680
    user_nonidle: int = 151681

    @property
    def state_ids(self) -> frozenset[int]:
        return frozenset(
            {
                self.user_complete,
                self.user_backchannel,
                self.user_incomplete,
                self.user_idle,
                self.user_nonidle,
            }
        )


@dataclass(frozen=True)
class AcousticWindow:
    samples: np.ndarray
    audio_back: np.ndarray
    process_chunk: np.ndarray
    audio_ahead: np.ndarray
    process_start_sample: int
    process_end_sample: int
    observed_through_sample: int
    source_audio: np.ndarray
    end_of_stream: bool = False


class RollingAcousticContext:
    """Reproduce the upstream 15,360/2,560/640 sample window explicitly."""

    def __init__(
        self,
        *,
        back_samples: int = 15_360,
        process_samples: int = 2_560,
        ahead_samples: int = 640,
        initial_padding: np.ndarray | None = None,
    ) -> None:
        if min(back_samples, process_samples, ahead_samples) < 0:
            raise ValueError("rolling-context sizes must be nonnegative")
        if process_samples == 0:
            raise ValueError("process_samples must be positive")
        self.back_samples = back_samples
        self.process_samples = process_samples
        self.ahead_samples = ahead_samples
        padding_size = back_samples + ahead_samples
        if initial_padding is None:
            initial_padding = np.zeros(padding_size, dtype=np.float32)
        padding = np.asarray(initial_padding, dtype=np.float32)
        if padding.ndim != 1 or len(padding) != padding_size:
            raise ValueError(
                "initial_padding must be one-dimensional with back+ahead samples"
            )
        if not np.isfinite(padding).all():
            raise ValueError("initial_padding contains non-finite samples")
        self._buffer = padding.copy()
        self._received_samples = 0
        self._window_index = 0
        self._finished = False

    @property
    def received_samples(self) -> int:
        return self._received_samples

    def push(self, samples: np.ndarray) -> list[AcousticWindow]:
        if self._finished:
            raise RuntimeError("cannot push audio after end-of-stream")
        samples = np.asarray(samples, dtype=np.float32)
        if samples.ndim != 1:
            raise ValueError("audio push must be one-dimensional")
        if not np.isfinite(samples).all():
            raise ValueError("audio push contains non-finite samples")
        self._received_samples += len(samples)
        if len(samples):
            self._buffer = np.concatenate((self._buffer, samples))

        return self._drain()

    def _drain(self) -> list[AcousticWindow]:

        required = self.back_samples + self.process_samples + self.ahead_samples
        windows: list[AcousticWindow] = []
        while len(self._buffer) >= required:
            back_end = self.back_samples
            process_end = back_end + self.process_samples
            full = self._buffer[:required].astype(np.float32, copy=True)
            raw_start = self._window_index * self.process_samples - self.ahead_samples
            raw_end = raw_start + self.process_samples
            source_start = max(0, raw_start)
            source_end = min(self._received_samples, max(0, raw_end))
            source_offset = source_start - raw_start
            source_count = source_end - source_start
            windows.append(
                AcousticWindow(
                    samples=full,
                    audio_back=full[:back_end],
                    process_chunk=full[back_end:process_end],
                    audio_ahead=full[process_end:],
                    process_start_sample=min(source_start, source_end),
                    process_end_sample=source_end,
                    observed_through_sample=min(
                        self._received_samples,
                        (self._window_index + 1) * self.process_samples,
                    ),
                    source_audio=full[
                        back_end + source_offset : back_end
                        + source_offset
                        + source_count
                    ].copy(),
                )
            )
            self._buffer = self._buffer[self.process_samples :]
            self._window_index += 1
        return windows

    def finish(self) -> list[AcousticWindow]:
        if self._finished:
            raise RuntimeError("end-of-stream was already processed")
        self._finished = True
        if self._received_samples == 0:
            return []
        target_windows = (
            self._received_samples + self.ahead_samples + self.process_samples - 1
        ) // self.process_samples
        windows: list[AcousticWindow] = []
        required = self.back_samples + self.process_samples + self.ahead_samples
        while self._window_index < target_windows:
            if len(self._buffer) < required:
                self._buffer = np.pad(self._buffer, (0, required - len(self._buffer)))
            windows.extend(self._drain())
        if windows:
            windows[-1] = replace(windows[-1], end_of_stream=True)
        return windows


@dataclass(frozen=True)
class TranscriptRevision:
    full_text: str
    delta_text: str
    corrected_previous_delta: str
    needs_correction: bool


class TranscriptRevisionBeyondCheckpoint(ValueError):
    """The recognizer revised text older than the single retained rollback point."""


class TranscriptRevisionTracker:
    """Map cumulative ASR text to append or one-chunk correction operations."""

    def __init__(
        self,
        *,
        encode: Callable[[str], list[int]],
        decode: Callable[[Sequence[int]], str],
    ) -> None:
        self._encode = encode
        self._decode = decode
        self._full_ids: list[int] = []
        self._last_delta_ids: list[int] = []

    def update(self, full_text: str) -> TranscriptRevision:
        if not isinstance(full_text, str):
            raise TypeError("transcript provider must return a string")
        new_ids = list(self._encode(full_text.strip()))
        old_ids = self._full_ids
        last_delta = self._last_delta_ids

        if new_ids[: len(old_ids)] == old_ids:
            delta_ids = new_ids[len(old_ids) :]
            result = TranscriptRevision(
                full_text=full_text,
                delta_text=self._decode(delta_ids) if delta_ids else "",
                corrected_previous_delta="",
                needs_correction=False,
            )
        else:
            stable_count = len(old_ids) - len(last_delta)
            if new_ids[:stable_count] != old_ids[:stable_count]:
                raise TranscriptRevisionBeyondCheckpoint(
                    "ASR revised text older than the retained SoulX checkpoint"
                )
            replacement_end = min(len(new_ids), len(old_ids))
            corrected_ids = new_ids[stable_count:replacement_end]
            delta_ids = new_ids[len(old_ids) :] if len(new_ids) > len(old_ids) else []
            result = TranscriptRevision(
                full_text=full_text,
                delta_text=self._decode(delta_ids) if delta_ids else "",
                corrected_previous_delta=(
                    self._decode(corrected_ids) if corrected_ids else ""
                ),
                needs_correction=True,
            )

        self._full_ids = new_ids
        self._last_delta_ids = delta_ids
        return result


@dataclass(frozen=True)
class ResolvedState:
    token_id: int
    mistake_count: int
    overridden: bool


def resolve_state_token(
    *,
    raw_token_id: int,
    previous_token_id: int | None,
    delta_text: str,
    mistake_count: int,
    complete_logit: float,
    incomplete_logit: float,
    token_ids: SoulXTokenIDs = SoulXTokenIDs(),
    max_mistakes: int = 3,
) -> ResolvedState:
    if raw_token_id not in token_ids.state_ids:
        raise ValueError(f"SoulX emitted an unknown state token: {raw_token_id}")
    if max_mistakes <= 0:
        raise ValueError("max_mistakes must be positive")
    if raw_token_id == token_ids.user_nonidle and not delta_text:
        mistake_count += 1
    else:
        mistake_count = 0
    boundary = (
        previous_token_id == token_ids.user_nonidle
        and raw_token_id == token_ids.user_idle
    ) or mistake_count >= max_mistakes
    if not boundary:
        return ResolvedState(raw_token_id, mistake_count, False)
    selected = (
        token_ids.user_complete
        if complete_logit > incomplete_logit
        else token_ids.user_incomplete
    )
    return ResolvedState(selected, mistake_count, True)


def cache_offsets(caches: Sequence[object]) -> tuple[int, ...]:
    offsets = []
    for index, cache in enumerate(caches):
        offset = getattr(cache, "offset", None)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError(f"cache[{index}] has no valid offset")
        offsets.append(offset)
    return tuple(offsets)


def restore_cache_offsets(caches: Sequence[object], offsets: Sequence[int]) -> None:
    if len(caches) != len(offsets):
        raise ValueError("cache checkpoint has a different layer count")
    for index, (cache, target) in enumerate(zip(caches, offsets, strict=True)):
        current = getattr(cache, "offset", None)
        trim = getattr(cache, "trim", None)
        if (
            isinstance(target, bool)
            or not isinstance(target, int)
            or target < 0
            or isinstance(current, bool)
            or not isinstance(current, int)
            or current < target
            or not callable(trim)
        ):
            raise ValueError(f"cache[{index}] cannot restore checkpoint {target}")
        removed = trim(current - target)
        if removed != current - target or getattr(cache, "offset", None) != target:
            raise ValueError(f"cache[{index}] did not restore checkpoint {target}")


class TranscriptProvider(Protocol):
    def transcribe(self, samples: np.ndarray, sample_rate: int) -> str: ...


@dataclass(frozen=True)
class SoulXChunkDecision:
    process_start_sample: int
    process_end_sample: int
    observed_through_sample: int
    audio_token_ids: tuple[int, ...]
    audio_check_token_id: int
    raw_state_token_id: int
    state_token_id: int
    state_overridden: bool
    transcript: TranscriptRevision


class SoulXStreamingController:
    """Execute the native learned SoulX loop while leaving policy outside it."""

    sample_rate = 16_000
    token_samples = 1_280
    audio_tokens_per_chunk = 2

    def __init__(
        self,
        *,
        core: object,
        tokenizer: object,
        feature_extractor: object,
        transcript_provider: TranscriptProvider,
        initial_padding: np.ndarray | None = None,
        token_ids: SoulXTokenIDs = SoulXTokenIDs(),
        max_mistakes: int = 3,
    ) -> None:
        from mlx_lm.models.cache import make_prompt_cache

        self.core = core
        self.tokenizer = tokenizer
        self.feature_extractor = feature_extractor
        self.transcript_provider = transcript_provider
        self.token_ids = token_ids
        self.max_mistakes = max_mistakes
        self.rolling = RollingAcousticContext(initial_padding=initial_padding)
        self.cache = make_prompt_cache(core.qwen)
        self._checkpoint: tuple[int, ...] | None = None
        self._previous_state: int | None = None
        self._mistake_count = 0
        self._cascade_audio = np.zeros(0, dtype=np.float32)
        self._transcripts = TranscriptRevisionTracker(
            encode=lambda text: tokenizer.encode(text, add_special_tokens=False),
            decode=lambda ids: tokenizer.decode(list(ids)),
        )
        prompt = mx.array(
            [token_ids.task_duplex_predict, token_ids.punctuation_off],
            dtype=mx.int32,
        )
        self._pending_embeddings = core.qwen.model.embed_tokens(prompt)

    def _embed_ids(self, ids: Sequence[int]) -> mx.array:
        return self.core.qwen.model.embed_tokens(
            mx.array(list(ids), dtype=mx.int32)
        )

    def _forward_embeddings(self, embeddings: mx.array) -> mx.array:
        logits = self.core.qwen(
            mx.zeros((1, embeddings.shape[0]), dtype=mx.int32),
            cache=self.cache,
            input_embeddings=embeddings[None],
        )
        mx.eval(logits, self.cache)
        return logits[0, -1]

    def _audio_embeddings(self, window: AcousticWindow) -> tuple[tuple[int, ...], mx.array]:
        stride = self.token_samples
        features = self.feature_extractor(
            [window.samples],
            sampling_rate=self.sample_rate,
            return_attention_mask=True,
            return_tensors="np",
            padding="longest",
            pad_to_multiple_of=stride,
        )
        token_ids, _ = self.core.whisper(
            mx.array(features["input_features"]),
            mx.array(features["attention_mask"]),
        )
        mx.eval(token_ids)
        start = self.rolling.back_samples // self.token_samples
        selected = tuple(
            int(value)
            for value in np.array(token_ids)[
                0, start : start + self.audio_tokens_per_chunk
            ]
        )
        if len(selected) != self.audio_tokens_per_chunk:
            raise ValueError("Whisper-VQ did not emit two tokens for the process chunk")
        projected = self.core.projector(
            self.core.whisper.codebook(mx.array(selected, dtype=mx.int32))
        )
        mx.eval(projected)
        return selected, projected

    def _process_window(self, window: AcousticWindow) -> SoulXChunkDecision:
        observe = getattr(self.transcript_provider, "observe", None)
        if callable(observe):
            observe(
                window.source_audio,
                self.sample_rate,
                final=window.end_of_stream,
            )
        self._cascade_audio = np.concatenate(
            (self._cascade_audio, window.process_chunk)
        )[-int(3.2 * self.sample_rate) :]
        audio_ids, audio_embeddings = self._audio_embeddings(window)
        audio_check_input = mx.concatenate(
            (self._pending_embeddings, audio_embeddings), axis=0
        )
        audio_logits = self._forward_embeddings(audio_check_input)
        audio_check = int(mx.argmax(audio_logits).item())

        revision = TranscriptRevision("", "", "", False)
        if audio_check != self.token_ids.audio_eos:
            full_text = self.transcript_provider.transcribe(
                self._cascade_audio.copy(), self.sample_rate
            )
            revision = self._transcripts.update(full_text)

        if revision.needs_correction:
            if self._checkpoint is None:
                raise TranscriptRevisionBeyondCheckpoint(
                    "ASR requested correction before a SoulX checkpoint existed"
                )
            restore_cache_offsets(self.cache, self._checkpoint)
            correction_ids = self.tokenizer.encode(
                revision.corrected_previous_delta, add_special_tokens=False
            )
            pieces = []
            if correction_ids:
                pieces.append(self._embed_ids(correction_ids))
            pieces.extend(
                (
                    self._embed_ids([self.token_ids.audio_eos]),
                    self._embed_ids([self.token_ids.user_nonidle]),
                    audio_embeddings,
                )
            )
            self._forward_embeddings(mx.concatenate(pieces, axis=0))

        self._checkpoint = cache_offsets(self.cache)
        delta_ids = self.tokenizer.encode(
            revision.delta_text, add_special_tokens=False
        )
        state_input_ids = [*delta_ids, self.token_ids.audio_eos]
        state_logits = self._forward_embeddings(self._embed_ids(state_input_ids))
        raw_state = int(mx.argmax(state_logits).item())
        resolved = resolve_state_token(
            raw_token_id=raw_state,
            previous_token_id=self._previous_state,
            delta_text=revision.delta_text,
            mistake_count=self._mistake_count,
            complete_logit=float(state_logits[self.token_ids.user_complete].item()),
            incomplete_logit=float(
                state_logits[self.token_ids.user_incomplete].item()
            ),
            token_ids=self.token_ids,
            max_mistakes=self.max_mistakes,
        )
        self._mistake_count = resolved.mistake_count
        self._previous_state = resolved.token_id
        self._pending_embeddings = self._embed_ids([resolved.token_id])
        return SoulXChunkDecision(
            process_start_sample=window.process_start_sample,
            process_end_sample=window.process_end_sample,
            observed_through_sample=window.observed_through_sample,
            audio_token_ids=audio_ids,
            audio_check_token_id=audio_check,
            raw_state_token_id=raw_state,
            state_token_id=resolved.token_id,
            state_overridden=resolved.overridden,
            transcript=revision,
        )

    def push_audio(self, samples: np.ndarray) -> list[SoulXChunkDecision]:
        return [self._process_window(window) for window in self.rolling.push(samples)]

    def finish(self) -> list[SoulXChunkDecision]:
        return [self._process_window(window) for window in self.rolling.finish()]
