"""Bounded push-audio state machine for MLX Audio Nemotron ASR.

The installed MLX Audio implementation is cache-aware internally but its public
generator receives a complete waveform. This adapter separates audio admission,
incremental log-mel extraction, encoder cache state, and RNN-T decoder state so
each admitted sample is processed once and results can be emitted while the
utterance is still arriving.

This module deliberately refuses unsupported preprocessing configurations. A
fallback that silently replays the growing waveform would look like streaming
while invalidating latency and compute measurements.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np


def safe_mel_frame_count(
    total_samples: int, *, hop_length: int, n_fft: int, final: bool
) -> int:
    """Return the exclusive mel-frame boundary safe to materialize.

    Nemotron's frontend uses a centered STFT. Before the stream ends, a frame is
    safe only when its complete right half is present. At end-of-stream, the
    canonical frontend right-pads and emits the remaining frames.
    """

    if total_samples < 0:
        raise ValueError("total_samples must be non-negative")
    if hop_length <= 0 or n_fft <= 0:
        raise ValueError("hop_length and n_fft must be positive")
    if final:
        return total_samples // hop_length + 1
    return max(0, (total_samples - n_fft // 2) // hop_length + 1)


class BoundedAudioBuffer:
    """Fixed-capacity mono float32 sample buffer with fail-before-mutation bounds."""

    def __init__(self, sample_rate: int, max_audio_seconds: float):
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if max_audio_seconds <= 0:
            raise ValueError("max_audio_seconds must be positive")
        self.sample_rate = int(sample_rate)
        self.capacity = int(round(sample_rate * max_audio_seconds))
        if self.capacity <= 0:
            raise ValueError("max_audio_seconds yields an empty buffer")
        self._samples = np.empty(self.capacity, dtype=np.float32)
        self.length = 0

    def append(self, samples: Iterable[float] | np.ndarray) -> int:
        values = np.asarray(samples, dtype=np.float32)
        if values.ndim != 1:
            raise ValueError("push audio must be one-dimensional mono samples")
        if values.size == 0:
            raise ValueError("push audio chunk must not be empty")
        if not np.isfinite(values).all():
            raise ValueError("push audio contains NaN or infinity")
        end = self.length + int(values.size)
        if end > self.capacity:
            raise OverflowError(
                f"push audio exceeds the {self.capacity / self.sample_rate:.3f}s stream bound"
            )
        self._samples[self.length : end] = values
        self.length = end
        return int(values.size)

    @property
    def samples(self) -> np.ndarray:
        return self._samples[: self.length]


class IncrementalNemotronMel:
    """Incremental, full-frontend-equivalent centered-STFT materializer."""

    def __init__(self, preprocess_args: Any, max_audio_seconds: float):
        if preprocess_args.normalize in ("per_feature", "all_features"):
            raise NotImplementedError(
                "incremental Nemotron input does not support utterance-global normalization"
            )
        if int(preprocess_args.pad_to) != 0:
            raise NotImplementedError(
                "incremental Nemotron input requires pad_to=0"
            )
        self.args = preprocess_args
        self.buffer = BoundedAudioBuffer(
            int(preprocess_args.sample_rate), max_audio_seconds
        )
        self.next_frame = 0
        self.closed = False

    def push(
        self, samples: Iterable[float] | np.ndarray | None, *, final: bool = False
    ):
        if self.closed:
            raise RuntimeError("cannot push audio after end-of-stream")
        if samples is None and not final:
            raise ValueError("non-final push requires an audio chunk")
        if samples is not None:
            values = np.asarray(samples, dtype=np.float32)
            if values.size:
                self.buffer.append(values)
            elif not final:
                raise ValueError("push audio chunk must not be empty")
        if final and self.buffer.length == 0:
            raise ValueError("cannot finalize an empty audio stream")

        target = safe_mel_frame_count(
            self.buffer.length,
            hop_length=int(self.args.hop_length),
            n_fft=int(self.args.n_fft),
            final=final,
        )
        mel = None
        if target > self.next_frame:
            mel = self._materialize_range(self.next_frame, target)
            self.next_frame = target
        if final:
            self.closed = True
        return mel

    def finish(self):
        return self.push(None, final=True)

    def _materialize_range(self, frame_start: int, frame_end: int):
        import mlx.core as mx
        from mlx_audio.stt.models.nemotron_asr.audio import (
            _padded_window,
            _power_to_log_mel,
            _preemphasize,
        )

        args = self.args
        hop = int(args.hop_length)
        n_fft = int(args.n_fft)
        num_frames = frame_end - frame_start
        sample_start = frame_start * hop - n_fft // 2
        sample_end = (frame_end - 1) * hop - n_fft // 2 + n_fft
        total_samples = self.buffer.length
        raw_start = max(sample_start, 0)
        raw_end = min(sample_end, total_samples)
        raw = mx.array(self.buffer.samples[raw_start:raw_end], dtype=mx.float32)

        if args.preemph and args.preemph > 0 and raw.shape[0] > 0:
            if raw_start > 0:
                previous = mx.array(
                    self.buffer.samples[raw_start - 1 : raw_start], dtype=mx.float32
                )
                first = raw[:1] - args.preemph * previous
                raw = mx.concatenate(
                    [first, raw[1:] - args.preemph * raw[:-1]], axis=0
                )
            else:
                raw = _preemphasize(raw, args)

        pieces = []
        left_pad = max(-sample_start, 0)
        right_pad = max(sample_end - total_samples, 0)
        if left_pad:
            pieces.append(mx.zeros((left_pad,), dtype=mx.float32))
        if raw.shape[0]:
            pieces.append(raw)
        if right_pad:
            pieces.append(mx.zeros((right_pad,), dtype=mx.float32))
        if not pieces:
            raise RuntimeError("mel frame range has no samples or padding")
        segment = pieces[0] if len(pieces) == 1 else mx.concatenate(pieces, axis=0)

        expected_len = (num_frames - 1) * hop + n_fft
        if segment.shape[0] < expected_len:
            segment = mx.pad(
                segment,
                ((0, expected_len - segment.shape[0]),),
                constant_values=0.0,
            )
        elif segment.shape[0] > expected_len:
            raise RuntimeError("incremental mel frame range exceeded expected length")

        frames = mx.as_strided(
            segment, shape=(num_frames, n_fft), strides=(hop, 1)
        )
        window = _padded_window(args)
        power = mx.square(mx.abs(mx.fft.rfft(frames * window))).astype(mx.float32)
        return _power_to_log_mel(power, args, mx.float32)


@dataclass(frozen=True)
class PushUpdate:
    result: Any
    received_samples: int
    encoder_audio_boundary_seconds: float
    final: bool


class NemotronPushStream:
    """One bounded Nemotron utterance with persistent frontend/model state."""

    def __init__(
        self,
        model: Any,
        *,
        language: str = "en-US",
        att_context_size: list[int] | tuple[int, int] | None = None,
        max_audio_seconds: float = 120.0,
    ):
        import mlx.core as mx

        self.mx = mx
        self.model = model
        self.language = language
        self.context = list(att_context_size or model.default_att_context_size)
        if len(self.context) != 2 or any(int(value) < 0 for value in self.context):
            raise ValueError("att_context_size must be [non-negative left, right]")
        self.context = [int(value) for value in self.context]
        self.frontend = IncrementalNemotronMel(
            model.preprocessor_config, max_audio_seconds
        )

        enc = model.encoder
        self.left_cache = self.context[0]
        self.right_context = self.context[1]
        self.encoder_chunk_frames = self.right_context + 1
        self.subsampling_factor = int(enc.args.subsampling_factor)
        self.encoder_frame_seconds = (
            self.subsampling_factor
            * int(model.preprocessor_config.hop_length)
            / self.sample_rate
        )
        self.mel_chunk_frames = self.encoder_chunk_frames * self.subsampling_factor
        self.conv_left = int(enc.args.conv_kernel_size) - 1
        self.attn_cache = [None] * len(enc.layers)
        self.conv_cache = [None] * len(enc.layers)
        self.mel_cache = None
        self.pending_mel = None
        self.encoder_mel_consumed = 0
        self.encoder_frames_emitted = 0
        self.encoder_blocks = 0

        self.last_token = int(model.blank_id)
        self.decoder_hidden = None
        self.hypothesis = []
        self.global_time = 0
        self.closed = False
        self.samples_admitted = 0

    @property
    def sample_rate(self) -> int:
        return int(self.model.preprocessor_config.sample_rate)

    def push_audio(
        self, samples: Iterable[float] | np.ndarray | None, *, final: bool = False
    ) -> list[PushUpdate]:
        if self.closed:
            raise RuntimeError("cannot push audio after end-of-stream")
        before = self.frontend.buffer.length
        new_mel = self.frontend.push(samples, final=final)
        self.samples_admitted += self.frontend.buffer.length - before
        if new_mel is not None and new_mel.shape[1]:
            self.pending_mel = (
                new_mel
                if self.pending_mel is None
                else self.mx.concatenate([self.pending_mel, new_mel], axis=1)
            )

        updates: list[PushUpdate] = []
        while self.pending_mel is not None and self.pending_mel.shape[1] > 0:
            if self.pending_mel.shape[1] < self.mel_chunk_frames and not final:
                break
            take = min(self.mel_chunk_frames, self.pending_mel.shape[1])
            if final and self.pending_mel.shape[1] <= self.mel_chunk_frames:
                take = self.pending_mel.shape[1]
            mel_chunk = self.pending_mel[:, :take]
            self.pending_mel = self.pending_mel[:, take:]
            is_final_chunk = final and self.pending_mel.shape[1] == 0
            prompted = self._encode_mel_chunk(mel_chunk, is_final=is_final_chunk)
            if prompted is None:
                continue
            result = self._decode_prompted(prompted)
            boundary = min(
                self.encoder_mel_consumed
                * int(self.model.preprocessor_config.hop_length)
                / self.sample_rate,
                self.samples_admitted / self.sample_rate,
            )
            updates.append(
                PushUpdate(
                    result=result,
                    received_samples=self.samples_admitted,
                    encoder_audio_boundary_seconds=boundary,
                    final=is_final_chunk,
                )
            )
            self.mx.clear_cache()

        if final:
            self.closed = True
            if self.pending_mel is not None and self.pending_mel.shape[1] != 0:
                raise RuntimeError("final push left unconsumed mel frames")
            expected_frames = safe_mel_frame_count(
                self.samples_admitted,
                hop_length=int(self.model.preprocessor_config.hop_length),
                n_fft=int(self.model.preprocessor_config.n_fft),
                final=True,
            )
            if self.frontend.next_frame != expected_frames:
                raise RuntimeError("final push did not materialize every canonical mel frame")
        return updates

    def finish(self) -> list[PushUpdate]:
        return self.push_audio(None, final=True)

    def _encode_mel_chunk(self, mel, *, is_final: bool):
        from mlx_audio.stt.models.nemotron_asr.streaming import (
            _PRE_ENCODE_MEL_CACHE,
            _stream_block,
        )

        enc = self.model.encoder
        cache_len = 0 if self.mel_cache is None else self.mel_cache.shape[1]
        window = (
            mel
            if self.mel_cache is None
            else self.mx.concatenate([self.mel_cache, mel], axis=1)
        )
        window_len = window.shape[1]
        sub = enc.pre_encode(
            window, self.mx.array([window_len], dtype=self.mx.int32)
        )[0]

        end = self.encoder_mel_consumed + mel.shape[1]
        base = (self.encoder_mel_consumed - cache_len) // self.subsampling_factor
        lo = self.encoder_frames_emitted - base
        hi = sub.shape[1] if is_final else end // self.subsampling_factor - base
        self.encoder_mel_consumed = end
        self.mel_cache = window[:, -_PRE_ENCODE_MEL_CACHE:]
        if hi <= lo:
            self.encoder_frames_emitted = base + max(lo, hi)
            return None

        self.encoder_frames_emitted = base + hi
        hidden = sub[:, lo:hi]
        for index, block in enumerate(enc.layers):
            hidden, self.attn_cache[index], self.conv_cache[index] = _stream_block(
                block,
                hidden,
                enc.pos_enc,
                self.attn_cache[index],
                self.conv_cache[index],
                self.left_cache,
                self.conv_left,
            )
        prompted = self.model.apply_prompt(hidden, self.language)
        state = [prompted, self.mel_cache]
        state.extend(value for value in self.attn_cache if value is not None)
        state.extend(value for value in self.conv_cache if value is not None)
        self.mx.eval(*state)
        self.encoder_blocks += 1
        return prompted

    def _decode_prompted(self, prompted):
        from mlx_audio.stt.models.nemo.alignment import (
            AlignedToken,
            sentences_to_result,
            tokens_to_sentences,
        )
        from mlx_audio.stt.models.nemotron_asr import tokenizer

        frame_seconds = (
            self.model.encoder_config.subsampling_factor
            * self.model.preprocessor_config.hop_length
            / self.model.preprocessor_config.sample_rate
        )
        chunk_len = prompted.shape[1]
        time_index = 0
        new_symbols = 0
        while time_index < chunk_len:
            feature = prompted[:, time_index : time_index + 1]
            current_token = (
                self.mx.array([[self.last_token]], dtype=self.mx.int32)
                if self.last_token != self.model.blank_id
                else None
            )
            decoder_output, (hidden, cell) = self.model.decoder(
                current_token, self.decoder_hidden
            )
            decoder_output = decoder_output.astype(feature.dtype)
            proposed_hidden = (
                hidden.astype(feature.dtype),
                cell.astype(feature.dtype),
            )
            joint_output = self.model.joint(feature, decoder_output)
            predicted = int(self.mx.argmax(joint_output))
            if predicted != self.model.blank_id:
                self.last_token = predicted
                self.decoder_hidden = proposed_hidden
                if not tokenizer.is_special_token(predicted, self.model.vocabulary):
                    self.hypothesis.append(
                        AlignedToken(
                            predicted,
                            start=(self.global_time + time_index) * frame_seconds,
                            duration=frame_seconds,
                            text=tokenizer.decode([predicted], self.model.vocabulary),
                        )
                    )
                new_symbols += 1
                if (
                    self.model.max_symbols is not None
                    and new_symbols >= self.model.max_symbols
                ):
                    time_index += 1
                    new_symbols = 0
            else:
                time_index += 1
                new_symbols = 0
        self.global_time += chunk_len
        if self.decoder_hidden is not None:
            self.mx.eval(*self.decoder_hidden)
        return sentences_to_result(tokens_to_sentences(self.hypothesis))

    def evidence(self) -> dict[str, object]:
        return {
            "push_audio_api": True,
            "source_audio_preloaded": False,
            "samples_admitted": self.samples_admitted,
            "audio_seconds_admitted": self.samples_admitted / self.sample_rate,
            "audio_buffer_capacity_samples": self.frontend.buffer.capacity,
            "mel_frames_materialized": self.frontend.next_frame,
            "encoder_mel_frames_consumed": self.encoder_mel_consumed,
            "encoder_frames_emitted": self.encoder_frames_emitted,
            "encoder_blocks": self.encoder_blocks,
            "attention_context": self.context,
            "lookahead_seconds": self.right_context * self.encoder_frame_seconds,
            "encoder_emit_chunk_seconds": self.encoder_chunk_frames
            * self.encoder_frame_seconds,
            "closed": self.closed,
            "replayed_audio_samples": 0,
        }
