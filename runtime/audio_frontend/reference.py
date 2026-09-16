"""Independent NumPy oracle for the portable C audio frontend."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MANIFEST_KEYS = (
    "schema",
    "schema_version",
    "id",
    "input_sample_type",
    "input_byte_order",
    "input_sample_rate_hz",
    "processing_sample_rate_hz",
    "input_channels",
    "mixing",
    "amplitude_scale_numerator",
    "amplitude_scale_denominator",
    "resampling",
    "resample_filter_taps",
    "resample_cutoff_ratio_ppm",
    "resample_group_delay_input_samples",
    "resample_left_boundary",
    "resample_terminal_tail",
    "preemphasis",
    "frame_length_samples",
    "hop_length_samples",
    "window",
    "terminal_padding",
    "fft_size",
    "spectrum",
    "mel_scale",
    "mel_bins",
    "mel_min_hz",
    "mel_max_hz",
    "log",
    "normalization",
    "output_layout",
    "lookahead_samples",
    "numerical_mode",
    "abs_tolerance_ppm",
    "rel_tolerance_ppm",
)
INTEGER_KEYS = {
    "schema_version",
    "input_sample_rate_hz",
    "processing_sample_rate_hz",
    "input_channels",
    "amplitude_scale_numerator",
    "amplitude_scale_denominator",
    "resample_filter_taps",
    "resample_cutoff_ratio_ppm",
    "resample_group_delay_input_samples",
    "frame_length_samples",
    "hop_length_samples",
    "fft_size",
    "mel_bins",
    "mel_min_hz",
    "mel_max_hz",
    "lookahead_samples",
    "abs_tolerance_ppm",
    "rel_tolerance_ppm",
}


@dataclass(frozen=True)
class FrontendConfig:
    identifier: str
    input_sample_rate_hz: int
    processing_sample_rate_hz: int
    resampling: str
    resample_filter_taps: int
    resample_cutoff_ratio: float
    resample_group_delay_input_samples: int
    frame_length: int
    hop_length: int
    fft_size: int
    mel_bins: int
    mel_min_hz: int
    mel_max_hz: int
    abs_tolerance: float
    rel_tolerance: float


@dataclass(frozen=True)
class FrontendOutput:
    normalized_pcm: np.ndarray
    resampled_pcm: np.ndarray
    frames: np.ndarray
    features: np.ndarray


def _expect(values: dict[str, str | int], key: str, expected: str | int) -> None:
    if values[key] != expected:
        raise ValueError(f"manifest {key} must equal {expected!r}")


def parse_manifest(manifest: bytes) -> FrontendConfig:
    """Parse the same canonical, ordered manifest grammar accepted by C."""
    if not manifest or not manifest.endswith(b"\n"):
        raise ValueError("manifest must be non-empty and newline terminated")
    if b"\0" in manifest or b"\r" in manifest:
        raise ValueError("manifest contains a forbidden byte")
    try:
        lines = manifest.decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError("manifest must be ASCII") from error
    pairs: list[tuple[str, str]] = []
    for line in lines:
        if "=" not in line:
            raise ValueError("manifest lines must be key=value")
        key, value = line.split("=", 1)
        if not key or not value:
            raise ValueError("manifest keys and values must be non-empty")
        pairs.append((key, value))
    if tuple(key for key, _ in pairs) != MANIFEST_KEYS:
        raise ValueError("manifest keys or order do not match the current grammar")
    values: dict[str, str | int] = {}
    for key, value in pairs:
        if key in INTEGER_KEYS:
            if not value.isascii() or not value.isdecimal():
                raise ValueError(f"manifest {key} must be an unsigned decimal integer")
            values[key] = int(value)
        else:
            values[key] = value

    _expect(values, "schema", "aiii.voice.frontend.manifest")
    _expect(values, "schema_version", 1)
    identifier = str(values["id"])
    if not identifier or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-"
        for character in identifier
    ):
        raise ValueError("manifest id must use lowercase letters, digits, and hyphens")
    for key, expected in {
        "input_sample_type": "pcm_s16le",
        "input_byte_order": "little",
        "input_channels": 1,
        "mixing": "mono_identity",
        "amplitude_scale_numerator": 1,
        "amplitude_scale_denominator": 32768,
        "resample_left_boundary": "zero_fill",
        "resample_terminal_tail": "none",
        "preemphasis": "none",
        "window": "hann_periodic",
        "terminal_padding": "right_zero_if_incomplete",
        "spectrum": "power",
        "mel_scale": "htk",
        "log": "log1p",
        "normalization": "none",
        "output_layout": "frames_bins_f32",
        "lookahead_samples": 0,
        "numerical_mode": "ieee754_float32_reference",
    }.items():
        _expect(values, key, expected)

    input_sample_rate = int(values["input_sample_rate_hz"])
    processing_sample_rate = int(values["processing_sample_rate_hz"])
    resampling = str(values["resampling"])
    resample_filter_taps = int(values["resample_filter_taps"])
    resample_cutoff_ratio_ppm = int(values["resample_cutoff_ratio_ppm"])
    resample_group_delay = int(values["resample_group_delay_input_samples"])
    frame_length = int(values["frame_length_samples"])
    hop_length = int(values["hop_length_samples"])
    fft_size = int(values["fft_size"])
    mel_bins = int(values["mel_bins"])
    mel_min = int(values["mel_min_hz"])
    mel_max = int(values["mel_max_hz"])
    abs_ppm = int(values["abs_tolerance_ppm"])
    rel_ppm = int(values["rel_tolerance_ppm"])
    if not 8000 <= input_sample_rate <= 192000:
        raise ValueError("manifest input sample rate is outside the supported range")
    if not 8000 <= processing_sample_rate <= 192000:
        raise ValueError("manifest processing sample rate is outside the supported range")
    if resampling == "none":
        if input_sample_rate != processing_sample_rate:
            raise ValueError("identity resampling requires equal sample rates")
        if any(
            (resample_filter_taps, resample_cutoff_ratio_ppm, resample_group_delay)
        ):
            raise ValueError("identity resampling requires zero filter parameters")
    elif resampling == "causal_windowed_sinc_hann":
        if (
            not 3 <= resample_filter_taps <= 127
            or resample_filter_taps % 2 == 0
        ):
            raise ValueError("causal resampling requires odd taps in [3, 127]")
        if not 1 <= resample_cutoff_ratio_ppm <= 1_000_000:
            raise ValueError("resample cutoff ratio must be in (0, 1]")
        if resample_group_delay != resample_filter_taps // 2:
            raise ValueError("resample group delay must equal half the filter order")
    else:
        raise ValueError("manifest resampling mode is unsupported")
    if not 1 <= frame_length <= 2048 or not 1 <= hop_length <= frame_length:
        raise ValueError("manifest frame or hop length is outside the supported range")
    if fft_size < frame_length or fft_size > 4096 or fft_size & (fft_size - 1):
        raise ValueError("manifest fft_size must be a supported power of two")
    if not 1 <= mel_bins <= 128:
        raise ValueError("manifest mel_bins is outside the supported range")
    if not 0 <= mel_min < mel_max < processing_sample_rate / 2:
        raise ValueError("manifest mel frequency range is invalid")
    if abs_ppm == 0 or rel_ppm == 0:
        raise ValueError("manifest tolerances must be positive")
    return FrontendConfig(
        identifier=identifier,
        input_sample_rate_hz=input_sample_rate,
        processing_sample_rate_hz=processing_sample_rate,
        resampling=resampling,
        resample_filter_taps=resample_filter_taps,
        resample_cutoff_ratio=resample_cutoff_ratio_ppm / 1_000_000,
        resample_group_delay_input_samples=resample_group_delay,
        frame_length=frame_length,
        hop_length=hop_length,
        fft_size=fft_size,
        mel_bins=mel_bins,
        mel_min_hz=mel_min,
        mel_max_hz=mel_max,
        abs_tolerance=abs_ppm / 1_000_000,
        rel_tolerance=rel_ppm / 1_000_000,
    )


def _mel_filterbank(config: FrontendConfig) -> np.ndarray:
    def hz_to_mel(hz: np.ndarray | float) -> np.ndarray | float:
        return 2595.0 * np.log10(1.0 + hz / 700.0)

    def mel_to_hz(mel: np.ndarray | float) -> np.ndarray | float:
        return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)

    mel_edges = np.linspace(
        hz_to_mel(config.mel_min_hz),
        hz_to_mel(config.mel_max_hz),
        config.mel_bins + 2,
        dtype=np.float64,
    )
    edges = mel_to_hz(mel_edges)
    frequencies = (
        np.arange(config.fft_size // 2 + 1, dtype=np.float64)
        * config.processing_sample_rate_hz
        / config.fft_size
    )
    filters = np.zeros((config.mel_bins, len(frequencies)), dtype=np.float64)
    for index in range(config.mel_bins):
        left, center, right = edges[index : index + 3]
        rising = (frequencies - left) / (center - left)
        falling = (right - frequencies) / (right - center)
        filters[index] = np.maximum(0.0, np.minimum(rising, falling))
    return filters


def _feature_frames(frames: np.ndarray, config: FrontendConfig) -> np.ndarray:
    if len(frames) == 0:
        return np.empty((0, config.mel_bins), dtype=np.float32)
    window = (0.5 - 0.5 * np.cos(
        2.0 * np.pi * np.arange(config.frame_length, dtype=np.float64)
        / config.frame_length
    )).astype(np.float32)
    spectrum = np.fft.rfft(frames * window, n=config.fft_size, axis=1)
    power = spectrum.real * spectrum.real + spectrum.imag * spectrum.imag
    features = np.log1p(power @ _mel_filterbank(config).T)
    return features.astype(np.float32)


class _ReferenceResampler:
    """Clear, non-production oracle for the manifest's causal resampler."""

    def __init__(self, config: FrontendConfig):
        self._config = config
        self._source = np.empty((0,), dtype=np.float32)
        self._emitted = 0
        self._coefficient_cache: dict[int, np.ndarray] = {}

    def _coefficients(self, phase_numerator: int) -> np.ndarray:
        cached = self._coefficient_cache.get(phase_numerator)
        if cached is not None:
            return cached
        config = self._config
        taps = config.resample_filter_taps
        positions = np.arange(taps, dtype=np.float64)
        fraction = phase_numerator / config.processing_sample_rate_hz
        distance = positions - config.resample_group_delay_input_samples + fraction
        cutoff = (
            0.5
            * min(
                1.0,
                config.processing_sample_rate_hz / config.input_sample_rate_hz,
            )
            * config.resample_cutoff_ratio
        )
        window = 0.5 - 0.5 * np.cos(2.0 * np.pi * positions / (taps - 1))
        coefficients = 2.0 * cutoff * np.sinc(2.0 * cutoff * distance) * window
        coefficient_sum = float(coefficients.sum())
        if not np.isfinite(coefficient_sum) or abs(coefficient_sum) < 1e-12:
            raise ValueError("resampler coefficient normalization is singular")
        result = coefficients / coefficient_sum
        self._coefficient_cache[phase_numerator] = result
        return result

    def push(self, normalized: np.ndarray) -> np.ndarray:
        normalized = np.asarray(normalized, dtype=np.float32)
        if normalized.size:
            self._source = np.concatenate((self._source, normalized))
        config = self._config
        target_count = (
            len(self._source) * config.processing_sample_rate_hz
            + config.input_sample_rate_hz
            - 1
        ) // config.input_sample_rate_hz
        if target_count == self._emitted:
            return np.empty((0,), dtype=np.float32)
        if config.resampling == "none":
            result = self._source[self._emitted : target_count].copy()
            self._emitted = target_count
            return result
        output = np.empty((target_count - self._emitted,), dtype=np.float32)
        for output_offset, output_index in enumerate(
            range(self._emitted, target_count)
        ):
            position_numerator = output_index * config.input_sample_rate_hz
            source_index, phase_numerator = divmod(
                position_numerator, config.processing_sample_rate_hz
            )
            coefficients = self._coefficients(phase_numerator)
            value = 0.0
            for tap, coefficient in enumerate(coefficients):
                if tap > source_index:
                    break
                value += float(coefficient) * float(self._source[source_index - tap])
            output[output_offset] = value
        self._emitted = target_count
        return output

    def reset(self) -> None:
        self._source = np.empty((0,), dtype=np.float32)
        self._emitted = 0


class ReferenceFrontend:
    """Stateful oracle with the same causal push/flush semantics as the C ABI."""

    def __init__(self, manifest: bytes):
        self.config = parse_manifest(manifest)
        self._resampler = _ReferenceResampler(self.config)
        self._pending = np.empty((0,), dtype=np.float32)
        self._flushed = False

    def push(self, pcm_s16le: bytes) -> FrontendOutput:
        if self._flushed:
            raise RuntimeError("frontend already flushed")
        if len(pcm_s16le) % 2:
            raise ValueError("PCM byte length must be divisible by two")
        normalized = np.frombuffer(pcm_s16le, dtype="<i2").astype(np.float32) / 32768.0
        resampled = self._resampler.push(normalized)
        self._pending = np.concatenate((self._pending, resampled))
        frames: list[np.ndarray] = []
        while len(self._pending) >= self.config.frame_length:
            frames.append(self._pending[: self.config.frame_length].copy())
            self._pending = self._pending[self.config.hop_length :]
        frame_array = (
            np.stack(frames).astype(np.float32)
            if frames
            else np.empty((0, self.config.frame_length), dtype=np.float32)
        )
        return FrontendOutput(
            normalized_pcm=normalized,
            resampled_pcm=resampled,
            frames=frame_array,
            features=_feature_frames(frame_array, self.config),
        )

    def flush(self) -> FrontendOutput:
        if self._flushed:
            raise RuntimeError("frontend already flushed")
        frames: list[np.ndarray] = []
        while len(self._pending):
            frame = np.zeros((self.config.frame_length,), dtype=np.float32)
            copied = min(len(self._pending), self.config.frame_length)
            frame[:copied] = self._pending[:copied]
            frames.append(frame)
            self._pending = self._pending[self.config.hop_length :]
        self._flushed = True
        frame_array = (
            np.stack(frames)
            if frames
            else np.empty((0, self.config.frame_length), dtype=np.float32)
        )
        return FrontendOutput(
            normalized_pcm=np.empty((0,), dtype=np.float32),
            resampled_pcm=np.empty((0,), dtype=np.float32),
            frames=frame_array,
            features=_feature_frames(frame_array, self.config),
        )

    def reset(self) -> None:
        self._pending = np.empty((0,), dtype=np.float32)
        self._resampler.reset()
        self._flushed = False
