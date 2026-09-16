"""Small causal turn controllers with optional tied depth and learned halting.

This module deliberately keeps the observation encoders, playback input, and
classification head common.  Experiments can therefore change the modality or
reasoning-depth mechanism without silently changing the surrounding system.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import mlx.core as mx
import mlx.nn as nn


Modality = Literal["acoustic", "transcript", "fused"]
DepthMode = Literal["single", "tied", "adaptive"]
FusionMode = Literal["pooled", "timeline"]


@dataclass(frozen=True)
class TurnControllerConfig:
    acoustic_dims: int = 80
    vocabulary_size: int = 258
    model_dims: int = 192
    hidden_dims: int = 512
    classes: int = 6
    completion_event_classes: int = 0
    modality: Modality = "fused"
    fusion_mode: FusionMode = "pooled"
    depth_mode: DepthMode = "single"
    recurrent_passes: int = 4
    halt_epsilon: float = 0.01

    def validate(self) -> None:
        if self.acoustic_dims <= 0 or self.vocabulary_size <= 1:
            raise ValueError("input dimensions must be positive")
        if self.model_dims <= 0 or self.hidden_dims <= 0 or self.classes <= 1:
            raise ValueError("model dimensions and classes must be positive")
        if self.completion_event_classes not in {0} and self.completion_event_classes <= 1:
            raise ValueError("completion event classes must be zero or greater than one")
        if self.modality not in {"acoustic", "transcript", "fused"}:
            raise ValueError(f"unsupported modality: {self.modality}")
        if self.depth_mode not in {"single", "tied", "adaptive"}:
            raise ValueError(f"unsupported depth mode: {self.depth_mode}")
        if self.fusion_mode not in {"pooled", "timeline"}:
            raise ValueError(f"unsupported fusion mode: {self.fusion_mode}")
        if self.fusion_mode == "timeline" and self.modality != "fused":
            raise ValueError("timeline fusion requires fused modality")
        if self.recurrent_passes <= 0:
            raise ValueError("recurrent_passes must be positive")
        if not 0 < self.halt_epsilon < 1:
            raise ValueError("halt_epsilon must be between zero and one")


class CausalAcousticEncoder(nn.Module):
    def __init__(self, input_dims: int, model_dims: int):
        super().__init__()
        self.input_norm = nn.LayerNorm(input_dims)
        self.input_projection = nn.Linear(input_dims, model_dims)
        self.recurrent = nn.GRU(model_dims, model_dims)
        self.output_norm = nn.LayerNorm(model_dims)

    def sequence(self, features: mx.array) -> mx.array:
        if features.ndim != 3:
            raise ValueError("acoustic features must have shape [batch, time, bins]")
        hidden = nn.gelu(self.input_projection(self.input_norm(features)))
        return self.output_norm(self.recurrent(hidden))

    def __call__(
        self, features: mx.array, lengths: mx.array | None = None
    ) -> mx.array:
        sequence = self.sequence(features)
        if sequence.shape[1] == 0:
            raise ValueError("acoustic feature sequence must be non-empty")
        if lengths is None:
            return sequence[:, -1]
        if lengths.ndim != 1 or lengths.shape[0] != sequence.shape[0]:
            raise ValueError("acoustic lengths must have shape [batch]")
        return sequence[mx.arange(sequence.shape[0]), lengths.astype(mx.int32) - 1]


class CausalTranscriptEncoder(nn.Module):
    def __init__(self, vocabulary_size: int, model_dims: int):
        super().__init__()
        self.embedding = nn.Embedding(vocabulary_size, model_dims)
        self.recurrent = nn.GRU(model_dims, model_dims)
        self.output_norm = nn.LayerNorm(model_dims)

    def sequence(self, token_ids: mx.array) -> mx.array:
        if token_ids.ndim != 2:
            raise ValueError("transcript tokens must have shape [batch, time]")
        if token_ids.shape[1] == 0:
            raise ValueError("transcript token sequence must be non-empty")
        return self.output_norm(self.recurrent(self.embedding(token_ids)))

    def __call__(
        self, token_ids: mx.array, lengths: mx.array | None = None
    ) -> mx.array:
        sequence = self.sequence(token_ids)
        if lengths is None:
            return sequence[:, -1]
        if lengths.ndim != 1 or lengths.shape[0] != sequence.shape[0]:
            raise ValueError("transcript lengths must have shape [batch]")
        return sequence[mx.arange(sequence.shape[0]), lengths.astype(mx.int32) - 1]


class GatedTiedBlock(nn.Module):
    """One residual transform whose parameters may be reused across depth."""

    def __init__(self, model_dims: int, hidden_dims: int):
        super().__init__()
        self.norm = nn.LayerNorm(model_dims)
        self.expand = nn.Linear(model_dims * 2, hidden_dims)
        self.contract = nn.Linear(hidden_dims, model_dims)
        self.gate = nn.Linear(model_dims * 2, model_dims)

    def __call__(self, state: mx.array, observation: mx.array) -> mx.array:
        joined = mx.concatenate([self.norm(state), observation], axis=-1)
        update = self.contract(nn.gelu(self.expand(joined)))
        gate = mx.sigmoid(self.gate(joined))
        return state + gate * update


class GatedTiedTemporalBlock(nn.Module):
    """One causal sequence transform whose parameters are reused across depth."""

    def __init__(self, model_dims: int):
        super().__init__()
        self.norm = nn.LayerNorm(model_dims)
        self.recurrent = nn.GRU(model_dims, model_dims)
        self.gate = nn.Linear(model_dims * 2, model_dims)

    def __call__(self, sequence: mx.array) -> mx.array:
        update = self.recurrent(self.norm(sequence))
        joined = mx.concatenate([sequence, update], axis=-1)
        return sequence + mx.sigmoid(self.gate(joined)) * update


class TurnController(nn.Module):
    def __init__(self, config: TurnControllerConfig):
        super().__init__()
        config.validate()
        self.config = config
        if config.modality in {"acoustic", "fused"}:
            self.acoustic = CausalAcousticEncoder(
                config.acoustic_dims, config.model_dims
            )
        if config.modality in {"transcript", "fused"}:
            self.transcript = CausalTranscriptEncoder(
                config.vocabulary_size, config.model_dims
            )
        observation_dims = config.model_dims * (2 if config.modality == "fused" else 1)
        self.observation_projection = nn.Linear(
            observation_dims + config.model_dims, config.model_dims
        )
        self.playback_embedding = nn.Embedding(2, config.model_dims)
        if config.fusion_mode == "timeline":
            self.temporal_block = GatedTiedTemporalBlock(config.model_dims)
        else:
            self.block = GatedTiedBlock(config.model_dims, config.hidden_dims)
        if config.depth_mode == "adaptive":
            self.halt = nn.Linear(config.model_dims, 1)
        self.output_norm = nn.LayerNorm(config.model_dims)
        self.classifier = nn.Linear(config.model_dims, config.classes)
        if config.completion_event_classes:
            self.completion_event_classifier = nn.Linear(
                config.model_dims, config.completion_event_classes
            )

    def _outputs(
        self, state: mx.array, expected_depth: mx.array
    ) -> dict[str, mx.array]:
        normalized = self.output_norm(state)
        result = {
            "logits": self.classifier(normalized),
            "expected_depth": expected_depth,
        }
        if self.config.completion_event_classes:
            result["completion_event_logits"] = self.completion_event_classifier(
                normalized
            )
        return result

    def _pooled_observation(
        self,
        acoustic: mx.array | None,
        transcript: mx.array | None,
        assistant_playing: mx.array,
        acoustic_lengths: mx.array | None,
        transcript_lengths: mx.array | None,
    ) -> mx.array:
        parts = []
        if self.config.modality in {"acoustic", "fused"}:
            if acoustic is None:
                raise ValueError("selected modality requires acoustic features")
            parts.append(self.acoustic(acoustic, acoustic_lengths))
        if self.config.modality in {"transcript", "fused"}:
            if transcript is None:
                raise ValueError("selected modality requires transcript tokens")
            parts.append(self.transcript(transcript, transcript_lengths))
        if assistant_playing.ndim != 1:
            raise ValueError("assistant_playing must have shape [batch]")
        observation = parts[0] if len(parts) == 1 else mx.concatenate(parts, axis=-1)
        playback = self.playback_embedding(assistant_playing.astype(mx.int32))
        return nn.gelu(
            self.observation_projection(mx.concatenate([observation, playback], axis=-1))
        )

    @staticmethod
    def _last(sequence: mx.array, lengths: mx.array | None) -> mx.array:
        if sequence.shape[1] == 0:
            raise ValueError("sequence must be non-empty")
        if lengths is None:
            return sequence[:, -1]
        if lengths.ndim != 1 or lengths.shape[0] != sequence.shape[0]:
            raise ValueError("sequence lengths must have shape [batch]")
        return sequence[mx.arange(sequence.shape[0]), lengths.astype(mx.int32) - 1]

    def _timeline_observation(
        self,
        acoustic: mx.array | None,
        transcript: mx.array | None,
        assistant_playing: mx.array,
        transcript_alignment: mx.array | None,
    ) -> mx.array:
        if acoustic is None or transcript is None:
            raise ValueError("timeline fusion requires acoustic and transcript inputs")
        if transcript_alignment is None:
            raise ValueError("timeline fusion requires transcript alignment")
        acoustic_sequence = self.acoustic.sequence(acoustic)
        transcript_sequence = self.transcript.sequence(transcript)
        if transcript_alignment.ndim != 2 or transcript_alignment.shape != acoustic.shape[:2]:
            raise ValueError("transcript alignment must have shape [batch, acoustic time]")
        if assistant_playing.ndim != 1 or assistant_playing.shape[0] != acoustic.shape[0]:
            raise ValueError("assistant_playing must have shape [batch]")
        indices = mx.broadcast_to(
            transcript_alignment.astype(mx.int32)[..., None],
            acoustic_sequence.shape,
        )
        aligned_transcript = mx.take_along_axis(transcript_sequence, indices, axis=1)
        playback = self.playback_embedding(assistant_playing.astype(mx.int32))
        playback = mx.broadcast_to(playback[:, None, :], acoustic_sequence.shape)
        return nn.gelu(
            self.observation_projection(
                mx.concatenate(
                    [acoustic_sequence, aligned_transcript, playback], axis=-1
                )
            )
        )

    def __call__(
        self,
        acoustic: mx.array | None,
        transcript: mx.array | None,
        assistant_playing: mx.array,
        acoustic_lengths: mx.array | None = None,
        transcript_lengths: mx.array | None = None,
        transcript_alignment: mx.array | None = None,
    ) -> dict[str, mx.array]:
        if self.config.fusion_mode == "timeline":
            sequence = self._timeline_observation(
                acoustic,
                transcript,
                assistant_playing,
                transcript_alignment,
            )
            expected_depth = mx.ones((sequence.shape[0],), dtype=sequence.dtype)
            if self.config.depth_mode == "single":
                sequence = self.temporal_block(sequence)
                state = self._last(sequence, acoustic_lengths)
            elif self.config.depth_mode == "tied":
                for _ in range(self.config.recurrent_passes):
                    sequence = self.temporal_block(sequence)
                state = self._last(sequence, acoustic_lengths)
                expected_depth = mx.full(
                    (sequence.shape[0],),
                    self.config.recurrent_passes,
                    dtype=sequence.dtype,
                )
            else:
                state = mx.zeros((sequence.shape[0], sequence.shape[-1]), dtype=sequence.dtype)
                remaining = mx.ones((sequence.shape[0],), dtype=sequence.dtype)
                expected_depth = mx.zeros_like(remaining)
                for index in range(self.config.recurrent_passes):
                    sequence = self.temporal_block(sequence)
                    candidate = self._last(sequence, acoustic_lengths)
                    if index + 1 == self.config.recurrent_passes:
                        weight = remaining
                    else:
                        probability = mx.sigmoid(
                            self.halt(self.output_norm(candidate))
                        ).squeeze(-1)
                        probability = mx.clip(
                            probability,
                            self.config.halt_epsilon,
                            1 - self.config.halt_epsilon,
                        )
                        weight = remaining * probability
                    state = state + weight[:, None] * candidate
                    expected_depth = expected_depth + weight * (index + 1)
                    remaining = remaining - weight
            return self._outputs(state, expected_depth)

        observation = self._pooled_observation(
            acoustic,
            transcript,
            assistant_playing,
            acoustic_lengths,
            transcript_lengths,
        )
        state = observation
        expected_depth = mx.ones((observation.shape[0],), dtype=observation.dtype)
        if self.config.depth_mode == "single":
            state = self.block(state, observation)
        elif self.config.depth_mode == "tied":
            for _ in range(self.config.recurrent_passes):
                state = self.block(state, observation)
            expected_depth = mx.full(
                (observation.shape[0],),
                self.config.recurrent_passes,
                dtype=observation.dtype,
            )
        else:
            mixture = mx.zeros_like(state)
            remaining = mx.ones((state.shape[0],), dtype=state.dtype)
            expected_depth = mx.zeros_like(remaining)
            for index in range(self.config.recurrent_passes):
                state = self.block(state, observation)
                if index + 1 == self.config.recurrent_passes:
                    weight = remaining
                else:
                    probability = mx.sigmoid(self.halt(self.output_norm(state))).squeeze(-1)
                    probability = mx.clip(
                        probability,
                        self.config.halt_epsilon,
                        1 - self.config.halt_epsilon,
                    )
                    weight = remaining * probability
                mixture = mixture + weight[:, None] * state
                expected_depth = expected_depth + weight * (index + 1)
                remaining = remaining - weight
            state = mixture
        return self._outputs(state, expected_depth)
