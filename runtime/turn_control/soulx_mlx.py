"""Torch-free MLX inference core for the sealed SoulX-Duplug checkpoint."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import mlx.core as mx
import mlx.nn as nn

from mlx_lm.models.qwen3 import Model as Qwen3Model
from mlx_lm.models.qwen3 import ModelArgs as Qwen3ModelArgs


@dataclass(frozen=True)
class WhisperVQArgs:
    num_mel_bins: int = 128
    d_model: int = 1280
    attention_heads: int = 20
    ffn_dim: int = 5120
    layers: int = 16
    max_source_positions: int = 1500
    pooled_positions: int = 375
    codebook_size: int = 16384
    pooling_kernel_size: int = 4
    block_size: int = 12
    layer_norm_eps: float = 1e-5


def block_causal_mask(attention_mask: mx.array, block_size: int) -> mx.array:
    """Allow the past and the complete current block, while masking padded keys."""
    if attention_mask.ndim != 2:
        raise ValueError("attention_mask must have shape [batch, time]")
    if block_size <= 0:
        raise ValueError("block_size must be positive")
    length = attention_mask.shape[1]
    query = mx.arange(length)[:, None]
    key = mx.arange(length)[None, :]
    causal = key <= query
    same_block = (query // block_size) == (key // block_size)
    valid_key = attention_mask.astype(mx.bool_)[:, None, None, :]
    allowed = (causal | same_block)[None, None, :, :] & valid_key
    return mx.where(allowed, mx.array(0.0), mx.array(-1e9))


def average_pool_time(
    hidden: mx.array, attention_mask: mx.array, kernel_size: int
) -> tuple[mx.array, mx.array]:
    if kernel_size <= 0:
        raise ValueError("kernel_size must be positive")
    padding = (-hidden.shape[1]) % kernel_size
    if padding:
        hidden = mx.pad(hidden, ((0, 0), (0, padding), (0, 0)))
    batch, length, width = hidden.shape
    hidden = hidden.reshape(batch, length // kernel_size, kernel_size, width).mean(2)
    return hidden, attention_mask[:, ::kernel_size]


class WhisperAttention(nn.Module):
    def __init__(self, args: WhisperVQArgs):
        super().__init__()
        self.heads = args.attention_heads
        self.head_dim = args.d_model // args.attention_heads
        self.scale = self.head_dim**-0.5
        self.k_proj = nn.Linear(args.d_model, args.d_model, bias=False)
        self.v_proj = nn.Linear(args.d_model, args.d_model, bias=True)
        self.q_proj = nn.Linear(args.d_model, args.d_model, bias=True)
        self.out_proj = nn.Linear(args.d_model, args.d_model, bias=True)

    def __call__(self, hidden: mx.array, mask: mx.array) -> mx.array:
        batch, length, _ = hidden.shape
        query = self.q_proj(hidden).reshape(batch, length, self.heads, self.head_dim)
        key = self.k_proj(hidden).reshape(batch, length, self.heads, self.head_dim)
        value = self.v_proj(hidden).reshape(batch, length, self.heads, self.head_dim)
        query = query.transpose(0, 2, 1, 3)
        key = key.transpose(0, 2, 1, 3)
        value = value.transpose(0, 2, 1, 3)
        attended = mx.fast.scaled_dot_product_attention(
            query, key, value, scale=self.scale, mask=mask
        )
        attended = attended.transpose(0, 2, 1, 3).reshape(
            batch, length, self.heads * self.head_dim
        )
        return self.out_proj(attended)


class WhisperVQBlock(nn.Module):
    def __init__(self, args: WhisperVQArgs):
        super().__init__()
        self.self_attn = WhisperAttention(args)
        self.self_attn_layer_norm = nn.LayerNorm(
            args.d_model, eps=args.layer_norm_eps
        )
        self.fc1 = nn.Linear(args.d_model, args.ffn_dim, bias=True)
        self.fc2 = nn.Linear(args.ffn_dim, args.d_model, bias=True)
        self.final_layer_norm = nn.LayerNorm(args.d_model, eps=args.layer_norm_eps)

    def __call__(self, hidden: mx.array, mask: mx.array) -> mx.array:
        hidden = hidden + self.self_attn(self.self_attn_layer_norm(hidden), mask)
        return hidden + self.fc2(nn.gelu(self.fc1(self.final_layer_norm(hidden))))


class WhisperVQEncoder(nn.Module):
    def __init__(self, args: WhisperVQArgs = WhisperVQArgs()):
        super().__init__()
        self.args = args
        self.conv1 = nn.Conv1d(
            args.num_mel_bins, args.d_model, kernel_size=3, stride=1, padding=0
        )
        self.conv2 = nn.Conv1d(
            args.d_model, args.d_model, kernel_size=3, stride=2, padding=0
        )
        self.embed_positions = nn.Embedding(args.max_source_positions, args.d_model)
        self.layers = [WhisperVQBlock(args) for _ in range(args.layers)]
        self.codebook = nn.Embedding(args.codebook_size, args.d_model)
        self.embed_positions2 = nn.Embedding(args.pooled_positions, args.d_model)

    @staticmethod
    def _causal_conv(convolution: nn.Conv1d, hidden: mx.array) -> mx.array:
        return convolution(mx.pad(hidden, ((0, 0), (2, 0), (0, 0))))

    def __call__(
        self, input_features: mx.array, attention_mask: mx.array
    ) -> tuple[mx.array, mx.array]:
        if input_features.ndim != 3:
            raise ValueError("input_features must have shape [batch, mel, time]")
        hidden = input_features.transpose(0, 2, 1)
        hidden = nn.gelu(self._causal_conv(self.conv1, hidden))
        hidden = nn.gelu(self._causal_conv(self.conv2, hidden))
        attention_mask = attention_mask[:, ::2]
        if attention_mask.shape[1] != hidden.shape[1]:
            raise ValueError("convolution output and attention mask lengths differ")
        if hidden.shape[1] > self.args.max_source_positions:
            raise ValueError("input exceeds Whisper-VQ positional capacity")
        hidden = hidden + self.embed_positions.weight[: hidden.shape[1]]
        mask = block_causal_mask(attention_mask, self.args.block_size)
        for layer in self.layers:
            hidden = layer(hidden, mask)
        hidden, attention_mask = average_pool_time(
            hidden, attention_mask, self.args.pooling_kernel_size
        )
        flat = hidden.reshape(-1, self.args.d_model)
        codebook = self.codebook.weight
        distances = (
            mx.sum(flat * flat, axis=1, keepdims=True)
            + mx.sum(codebook * codebook, axis=1)[None, :]
            - 2 * (flat @ codebook.T)
        )
        token_ids = mx.argmin(distances, axis=1).reshape(
            hidden.shape[0], hidden.shape[1]
        )
        quantized = self.codebook(token_ids)
        quantized = quantized + self.embed_positions2.weight[: quantized.shape[1]]
        return token_ids, quantized


class AudioProjector(nn.Module):
    def __init__(self):
        super().__init__()
        self.linear1 = nn.Linear(1280, 2048, bias=True)
        self.linear2 = nn.Linear(2048, 2048, bias=True)
        self.linear3 = nn.Linear(2048, 1024, bias=True)

    def __call__(self, hidden: mx.array) -> mx.array:
        hidden = nn.relu(self.linear1(hidden))
        hidden = nn.relu(self.linear2(hidden))
        return self.linear3(hidden)


@dataclass
class SoulXMLXCore:
    whisper: WhisperVQEncoder
    projector: AudioProjector
    qwen: Qwen3Model


def load_core(artifact: Path, qwen_config: dict) -> SoulXMLXCore:
    whisper = WhisperVQEncoder()
    projector = AudioProjector()
    qwen = Qwen3Model(Qwen3ModelArgs.from_dict(qwen_config))
    for module, name in (
        (whisper, "whisper-fp32.safetensors"),
        (projector, "projector-fp32.safetensors"),
        (qwen, "qwen-fp32.safetensors"),
    ):
        weights = mx.load(str(artifact / name))
        module.load_weights(list(weights.items()), strict=True)
    return SoulXMLXCore(whisper=whisper, projector=projector, qwen=qwen)
