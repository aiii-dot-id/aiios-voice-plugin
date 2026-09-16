#include "aiii_voice_frontend.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#define VF_MAX_ID_BYTES 96u
#define VF_MAX_FRAME_SAMPLES 2048u
#define VF_MAX_FFT_SIZE 4096u
#define VF_MAX_MEL_BINS 128u
#define VF_MAX_RESAMPLE_TAPS 127u
#define VF_PI 3.14159265358979323846264338327950288

#define VF_RESAMPLING_NONE 0u
#define VF_RESAMPLING_CAUSAL_SINC_HANN 1u

typedef struct vf_config {
    char id[VF_MAX_ID_BYTES];
    size_t input_sample_rate_hz;
    size_t processing_sample_rate_hz;
    size_t resampling;
    size_t resample_filter_taps;
    size_t resample_cutoff_ratio_ppm;
    size_t resample_group_delay_input_samples;
    size_t frame_length;
    size_t hop_length;
    size_t fft_size;
    size_t mel_bins;
    size_t mel_min_hz;
    size_t mel_max_hz;
    size_t abs_tolerance_ppm;
    size_t rel_tolerance_ppm;
} vf_config;

struct vf_frontend {
    vf_config config;
    float *pending;
    size_t pending_length;
    size_t pending_capacity;
    float source_history[VF_MAX_RESAMPLE_TAPS];
    size_t input_samples_received;
    size_t resampled_samples_emitted;
    size_t next_source_index;
    size_t next_phase_numerator;
    int flushed;
};

typedef struct vf_parser {
    const uint8_t *position;
    const uint8_t *end;
} vf_parser;

static int vf_bytes_equal(const uint8_t *value, size_t length, const char *expected) {
    const size_t expected_length = strlen(expected);
    return length == expected_length && memcmp(value, expected, length) == 0;
}

static int vf_next_value(
    vf_parser *parser,
    const char *key,
    const uint8_t **out_value,
    size_t *out_length
) {
    const size_t key_length = strlen(key);
    const uint8_t *line_end;
    const uint8_t *value;
    if (parser == NULL || out_value == NULL || out_length == NULL) {
        return 0;
    }
    if ((size_t)(parser->end - parser->position) <= key_length + 1u) {
        return 0;
    }
    if (memcmp(parser->position, key, key_length) != 0 ||
        parser->position[key_length] != '=') {
        return 0;
    }
    value = parser->position + key_length + 1u;
    line_end = memchr(value, '\n', (size_t)(parser->end - value));
    if (line_end == NULL || line_end == value) {
        return 0;
    }
    *out_value = value;
    *out_length = (size_t)(line_end - value);
    parser->position = line_end + 1u;
    return 1;
}

static int vf_expect(vf_parser *parser, const char *key, const char *expected) {
    const uint8_t *value;
    size_t length;
    return vf_next_value(parser, key, &value, &length) &&
           vf_bytes_equal(value, length, expected);
}

static int vf_unsigned(vf_parser *parser, const char *key, size_t *out_value) {
    const uint8_t *value;
    size_t length;
    size_t result = 0u;
    size_t index;
    if (!vf_next_value(parser, key, &value, &length)) {
        return 0;
    }
    for (index = 0u; index < length; ++index) {
        const unsigned digit = (unsigned)(value[index] - (uint8_t)'0');
        if (digit > 9u || result > (SIZE_MAX - digit) / 10u) {
            return 0;
        }
        result = result * 10u + digit;
    }
    *out_value = result;
    return 1;
}

static int vf_identifier(vf_parser *parser, const char *key, char *out, size_t capacity) {
    const uint8_t *value;
    size_t length;
    size_t index;
    if (!vf_next_value(parser, key, &value, &length) || length >= capacity) {
        return 0;
    }
    for (index = 0u; index < length; ++index) {
        const uint8_t character = value[index];
        const int allowed =
            (character >= (uint8_t)'a' && character <= (uint8_t)'z') ||
            (character >= (uint8_t)'0' && character <= (uint8_t)'9') ||
            character == (uint8_t)'-';
        if (!allowed) {
            return 0;
        }
    }
    memcpy(out, value, length);
    out[length] = '\0';
    return 1;
}

static int vf_resampling_mode(vf_parser *parser, size_t *out_mode) {
    const uint8_t *value;
    size_t length;
    if (!vf_next_value(parser, "resampling", &value, &length)) {
        return 0;
    }
    if (vf_bytes_equal(value, length, "none")) {
        *out_mode = VF_RESAMPLING_NONE;
        return 1;
    }
    if (vf_bytes_equal(value, length, "causal_windowed_sinc_hann")) {
        *out_mode = VF_RESAMPLING_CAUSAL_SINC_HANN;
        return 1;
    }
    return 0;
}

static int vf_power_of_two(size_t value) {
    return value != 0u && (value & (value - 1u)) == 0u;
}

static int vf_parse_manifest(const uint8_t *bytes, size_t size, vf_config *config) {
    vf_parser parser;
    size_t channels;
    size_t amplitude_numerator;
    size_t amplitude_denominator;
    size_t lookahead;
    if (bytes == NULL || size == 0u || config == NULL || bytes[size - 1u] != '\n') {
        return 0;
    }
    if (memchr(bytes, '\0', size) != NULL || memchr(bytes, '\r', size) != NULL) {
        return 0;
    }
    memset(config, 0, sizeof(*config));
    parser.position = bytes;
    parser.end = bytes + size;
    if (!vf_expect(&parser, "schema", "aiii.voice.frontend.manifest") ||
        !vf_expect(&parser, "schema_version", "1") ||
        !vf_identifier(&parser, "id", config->id, sizeof(config->id)) ||
        !vf_expect(&parser, "input_sample_type", "pcm_s16le") ||
        !vf_expect(&parser, "input_byte_order", "little") ||
        !vf_unsigned(
            &parser,
            "input_sample_rate_hz",
            &config->input_sample_rate_hz
        ) ||
        !vf_unsigned(
            &parser,
            "processing_sample_rate_hz",
            &config->processing_sample_rate_hz
        ) ||
        !vf_unsigned(&parser, "input_channels", &channels) ||
        !vf_expect(&parser, "mixing", "mono_identity") ||
        !vf_unsigned(&parser, "amplitude_scale_numerator", &amplitude_numerator) ||
        !vf_unsigned(&parser, "amplitude_scale_denominator", &amplitude_denominator) ||
        !vf_resampling_mode(&parser, &config->resampling) ||
        !vf_unsigned(
            &parser,
            "resample_filter_taps",
            &config->resample_filter_taps
        ) ||
        !vf_unsigned(
            &parser,
            "resample_cutoff_ratio_ppm",
            &config->resample_cutoff_ratio_ppm
        ) ||
        !vf_unsigned(
            &parser,
            "resample_group_delay_input_samples",
            &config->resample_group_delay_input_samples
        ) ||
        !vf_expect(&parser, "resample_left_boundary", "zero_fill") ||
        !vf_expect(&parser, "resample_terminal_tail", "none") ||
        !vf_expect(&parser, "preemphasis", "none") ||
        !vf_unsigned(&parser, "frame_length_samples", &config->frame_length) ||
        !vf_unsigned(&parser, "hop_length_samples", &config->hop_length) ||
        !vf_expect(&parser, "window", "hann_periodic") ||
        !vf_expect(&parser, "terminal_padding", "right_zero_if_incomplete") ||
        !vf_unsigned(&parser, "fft_size", &config->fft_size) ||
        !vf_expect(&parser, "spectrum", "power") ||
        !vf_expect(&parser, "mel_scale", "htk") ||
        !vf_unsigned(&parser, "mel_bins", &config->mel_bins) ||
        !vf_unsigned(&parser, "mel_min_hz", &config->mel_min_hz) ||
        !vf_unsigned(&parser, "mel_max_hz", &config->mel_max_hz) ||
        !vf_expect(&parser, "log", "log1p") ||
        !vf_expect(&parser, "normalization", "none") ||
        !vf_expect(&parser, "output_layout", "frames_bins_f32") ||
        !vf_unsigned(&parser, "lookahead_samples", &lookahead) ||
        !vf_expect(&parser, "numerical_mode", "ieee754_float32_reference") ||
        !vf_unsigned(&parser, "abs_tolerance_ppm", &config->abs_tolerance_ppm) ||
        !vf_unsigned(&parser, "rel_tolerance_ppm", &config->rel_tolerance_ppm) ||
        parser.position != parser.end) {
        return 0;
    }
    if (channels != 1u || amplitude_numerator != 1u ||
        amplitude_denominator != 32768u ||
        config->input_sample_rate_hz < 8000u ||
        config->input_sample_rate_hz > 192000u ||
        config->processing_sample_rate_hz < 8000u ||
        config->processing_sample_rate_hz > 192000u ||
        config->frame_length == 0u ||
        config->frame_length > VF_MAX_FRAME_SAMPLES || config->hop_length == 0u ||
        config->hop_length > config->frame_length ||
        !vf_power_of_two(config->fft_size) ||
        config->fft_size < config->frame_length ||
        config->fft_size > VF_MAX_FFT_SIZE || config->mel_bins == 0u ||
        config->mel_bins > VF_MAX_MEL_BINS ||
        config->mel_min_hz >= config->mel_max_hz ||
        config->mel_max_hz >= config->processing_sample_rate_hz / 2u ||
        lookahead != 0u ||
        config->abs_tolerance_ppm == 0u || config->rel_tolerance_ppm == 0u) {
        return 0;
    }
    if (config->resampling == VF_RESAMPLING_NONE) {
        if (config->input_sample_rate_hz != config->processing_sample_rate_hz ||
            config->resample_filter_taps != 0u ||
            config->resample_cutoff_ratio_ppm != 0u ||
            config->resample_group_delay_input_samples != 0u) {
            return 0;
        }
    } else if (config->resampling == VF_RESAMPLING_CAUSAL_SINC_HANN) {
        if (config->resample_filter_taps < 3u ||
            config->resample_filter_taps > VF_MAX_RESAMPLE_TAPS ||
            config->resample_filter_taps % 2u == 0u ||
            config->resample_cutoff_ratio_ppm == 0u ||
            config->resample_cutoff_ratio_ppm > 1000000u ||
            config->resample_group_delay_input_samples !=
                config->resample_filter_taps / 2u) {
            return 0;
        }
    } else {
        return 0;
    }
    return 1;
}

static int vf_reserve(vf_frontend *frontend, size_t additional) {
    size_t required;
    size_t capacity;
    float *replacement;
    if (additional > SIZE_MAX - frontend->pending_length) {
        return 0;
    }
    required = frontend->pending_length + additional;
    if (required <= frontend->pending_capacity) {
        return 1;
    }
    capacity = frontend->pending_capacity == 0u ? frontend->config.frame_length :
                                                  frontend->pending_capacity;
    while (capacity < required) {
        if (capacity > SIZE_MAX / 2u) {
            capacity = required;
            break;
        }
        capacity *= 2u;
    }
    if (capacity > SIZE_MAX / sizeof(float)) {
        return 0;
    }
    replacement = (float *)realloc(frontend->pending, capacity * sizeof(float));
    if (replacement == NULL) {
        return 0;
    }
    frontend->pending = replacement;
    frontend->pending_capacity = capacity;
    return 1;
}

static void vf_fft(float *real, float *imaginary, size_t size) {
    size_t i;
    size_t j = 0u;
    for (i = 1u; i < size; ++i) {
        size_t bit = size >> 1u;
        while ((j & bit) != 0u) {
            j ^= bit;
            bit >>= 1u;
        }
        j ^= bit;
        if (i < j) {
            const float real_swap = real[i];
            const float imaginary_swap = imaginary[i];
            real[i] = real[j];
            imaginary[i] = imaginary[j];
            real[j] = real_swap;
            imaginary[j] = imaginary_swap;
        }
    }
    for (i = 2u; i <= size; i <<= 1u) {
        const float angle = (float)(-2.0 * VF_PI / (double)i);
        const float step_real = cosf(angle);
        const float step_imaginary = sinf(angle);
        size_t base;
        for (base = 0u; base < size; base += i) {
            float twiddle_real = 1.0f;
            float twiddle_imaginary = 0.0f;
            size_t offset;
            for (offset = 0u; offset < i / 2u; ++offset) {
                const size_t even = base + offset;
                const size_t odd = even + i / 2u;
                const float odd_real = real[odd] * twiddle_real -
                                       imaginary[odd] * twiddle_imaginary;
                const float odd_imaginary = real[odd] * twiddle_imaginary +
                                            imaginary[odd] * twiddle_real;
                const float next_twiddle_real = twiddle_real * step_real -
                                                twiddle_imaginary * step_imaginary;
                twiddle_imaginary = twiddle_real * step_imaginary +
                                     twiddle_imaginary * step_real;
                twiddle_real = next_twiddle_real;
                real[odd] = real[even] - odd_real;
                imaginary[odd] = imaginary[even] - odd_imaginary;
                real[even] += odd_real;
                imaginary[even] += odd_imaginary;
            }
        }
        if (i == size) {
            break;
        }
    }
}

static double vf_hz_to_mel(double hz) {
    return 2595.0 * log10(1.0 + hz / 700.0);
}

static double vf_mel_to_hz(double mel) {
    return 700.0 * (pow(10.0, mel / 2595.0) - 1.0);
}

static void vf_emit_frame(const vf_frontend *frontend, float *output) {
    float real[VF_MAX_FFT_SIZE];
    float imaginary[VF_MAX_FFT_SIZE];
    double mel_edges[VF_MAX_MEL_BINS + 2u];
    const vf_config *config = &frontend->config;
    const size_t spectrum_bins = config->fft_size / 2u + 1u;
    const double mel_min = vf_hz_to_mel((double)config->mel_min_hz);
    const double mel_max = vf_hz_to_mel((double)config->mel_max_hz);
    size_t index;
    size_t mel_bin;
    memset(real, 0, config->fft_size * sizeof(float));
    memset(imaginary, 0, config->fft_size * sizeof(float));
    for (index = 0u; index < config->frame_length; ++index) {
        const float sample = index < frontend->pending_length ?
                                 frontend->pending[index] : 0.0f;
        const float window = (float)(0.5 - 0.5 * cos(
            2.0 * VF_PI * (double)index / (double)config->frame_length
        ));
        real[index] = sample * window;
    }
    vf_fft(real, imaginary, config->fft_size);
    for (index = 0u; index < config->mel_bins + 2u; ++index) {
        const double mel = mel_min + (mel_max - mel_min) * (double)index /
                                       (double)(config->mel_bins + 1u);
        mel_edges[index] = vf_mel_to_hz(mel);
    }
    for (mel_bin = 0u; mel_bin < config->mel_bins; ++mel_bin) {
        const double left = mel_edges[mel_bin];
        const double center = mel_edges[mel_bin + 1u];
        const double right = mel_edges[mel_bin + 2u];
        double energy = 0.0;
        for (index = 0u; index < spectrum_bins; ++index) {
            const double frequency =
                (double)index * (double)config->processing_sample_rate_hz /
                (double)config->fft_size;
            double weight = 0.0;
            if (frequency >= left && frequency <= center) {
                weight = (frequency - left) / (center - left);
            } else if (frequency > center && frequency <= right) {
                weight = (right - frequency) / (right - center);
            }
            if (weight > 0.0) {
                const double power = (double)real[index] * (double)real[index] +
                                     (double)imaginary[index] * (double)imaginary[index];
                energy += power * weight;
            }
        }
        output[mel_bin] = (float)log1p(energy);
    }
}

static void vf_consume_hop(vf_frontend *frontend) {
    const size_t consumed = frontend->pending_length < frontend->config.hop_length ?
                                frontend->pending_length : frontend->config.hop_length;
    frontend->pending_length -= consumed;
    if (frontend->pending_length != 0u) {
        memmove(
            frontend->pending,
            frontend->pending + consumed,
            frontend->pending_length * sizeof(float)
        );
    }
}

uint32_t vf_frontend_abi_version(void) {
    return VF_FRONTEND_ABI_VERSION;
}

vf_status vf_frontend_create(
    const uint8_t *manifest,
    size_t manifest_size,
    vf_frontend **out_frontend
) {
    vf_config config;
    vf_frontend *frontend;
    if (out_frontend == NULL) {
        return VF_INVALID_ARGUMENT;
    }
    *out_frontend = NULL;
    if (!vf_parse_manifest(manifest, manifest_size, &config)) {
        return VF_INVALID_MANIFEST;
    }
    frontend = (vf_frontend *)calloc(1u, sizeof(*frontend));
    if (frontend == NULL) {
        return VF_OUT_OF_MEMORY;
    }
    frontend->config = config;
    *out_frontend = frontend;
    return VF_OK;
}

static int vf_resampled_total(
    const vf_config *config,
    size_t input_samples,
    size_t *out_samples
) {
    const size_t quotient = input_samples / config->input_sample_rate_hz;
    const size_t remainder = input_samples % config->input_sample_rate_hz;
    size_t result;
    size_t remainder_numerator;
    if (quotient > SIZE_MAX / config->processing_sample_rate_hz) {
        return 0;
    }
    result = quotient * config->processing_sample_rate_hz;
    if (remainder == 0u) {
        *out_samples = result;
        return 1;
    }
    if (remainder >
        (SIZE_MAX - (config->input_sample_rate_hz - 1u)) /
            config->processing_sample_rate_hz) {
        return 0;
    }
    remainder_numerator =
        remainder * config->processing_sample_rate_hz +
        config->input_sample_rate_hz - 1u;
    remainder_numerator /= config->input_sample_rate_hz;
    if (remainder_numerator > SIZE_MAX - result) {
        return 0;
    }
    *out_samples = result + remainder_numerator;
    return 1;
}

static void vf_add_processing_sample(
    vf_frontend *frontend,
    float sample,
    float *output,
    size_t *emitted
) {
    frontend->pending[frontend->pending_length] = sample;
    ++frontend->pending_length;
    if (frontend->pending_length >= frontend->config.frame_length) {
        vf_emit_frame(
            frontend,
            output + *emitted * frontend->config.mel_bins
        );
        vf_consume_hop(frontend);
        ++*emitted;
    }
}

static float vf_resample_current(const vf_frontend *frontend) {
    const vf_config *config = &frontend->config;
    double coefficients[VF_MAX_RESAMPLE_TAPS];
    const double fraction =
        (double)frontend->next_phase_numerator /
        (double)config->processing_sample_rate_hz;
    const double rate_ratio =
        (double)config->processing_sample_rate_hz /
        (double)config->input_sample_rate_hz;
    const double cutoff =
        0.5 * (rate_ratio < 1.0 ? rate_ratio : 1.0) *
        (double)config->resample_cutoff_ratio_ppm / 1000000.0;
    double coefficient_sum = 0.0;
    double value = 0.0;
    size_t tap;
    for (tap = 0u; tap < config->resample_filter_taps; ++tap) {
        const double distance =
            (double)tap -
            (double)config->resample_group_delay_input_samples + fraction;
        const double argument = 2.0 * cutoff * distance;
        const double sinc = argument == 0.0 ?
                                1.0 : sin(VF_PI * argument) /
                                          (VF_PI * argument);
        const double window =
            0.5 - 0.5 * cos(
                2.0 * VF_PI * (double)tap /
                (double)(config->resample_filter_taps - 1u)
            );
        coefficients[tap] = 2.0 * cutoff * sinc * window;
        coefficient_sum += coefficients[tap];
    }
    for (tap = 0u;
         tap < config->resample_filter_taps &&
         tap <= frontend->next_source_index;
         ++tap) {
        const size_t source_index = frontend->next_source_index - tap;
        value +=
            coefficients[tap] / coefficient_sum *
            (double)frontend->source_history[
                source_index % config->resample_filter_taps
            ];
    }
    return (float)value;
}

static void vf_advance_resampler(vf_frontend *frontend) {
    const size_t phase =
        frontend->next_phase_numerator + frontend->config.input_sample_rate_hz;
    frontend->next_source_index +=
        phase / frontend->config.processing_sample_rate_hz;
    frontend->next_phase_numerator =
        phase % frontend->config.processing_sample_rate_hz;
    ++frontend->resampled_samples_emitted;
}

static size_t vf_checked_frame_capacity(
    const vf_frontend *frontend,
    size_t frames
) {
    if (frames > SIZE_MAX / frontend->config.mel_bins) {
        return SIZE_MAX;
    }
    return frames;
}

size_t vf_frontend_feature_bins(const vf_frontend *frontend) {
    return frontend == NULL ? 0u : frontend->config.mel_bins;
}

size_t vf_frontend_push_frame_capacity(
    const vf_frontend *frontend,
    size_t input_samples
) {
    size_t total_input;
    size_t total_resampled;
    size_t additional_resampled;
    size_t available;
    if (frontend == NULL || frontend->flushed) {
        return SIZE_MAX;
    }
    if (input_samples > SIZE_MAX - frontend->input_samples_received) {
        return SIZE_MAX;
    }
    total_input = frontend->input_samples_received + input_samples;
    if (frontend->config.resampling != VF_RESAMPLING_NONE &&
        total_input > SIZE_MAX - frontend->config.input_sample_rate_hz) {
        return SIZE_MAX;
    }
    if (!vf_resampled_total(&frontend->config, total_input, &total_resampled) ||
        total_resampled < frontend->resampled_samples_emitted) {
        return SIZE_MAX;
    }
    additional_resampled =
        total_resampled - frontend->resampled_samples_emitted;
    if (additional_resampled > SIZE_MAX - frontend->pending_length) {
        return SIZE_MAX;
    }
    available = frontend->pending_length + additional_resampled;
    if (available < frontend->config.frame_length) {
        return 0u;
    }
    return vf_checked_frame_capacity(
        frontend,
        1u + (available - frontend->config.frame_length) /
                 frontend->config.hop_length
    );
}

size_t vf_frontend_flush_frame_capacity(const vf_frontend *frontend) {
    if (frontend == NULL || frontend->flushed) {
        return SIZE_MAX;
    }
    if (frontend->pending_length == 0u) {
        return 0u;
    }
    return vf_checked_frame_capacity(
        frontend,
        1u + (frontend->pending_length - 1u) / frontend->config.hop_length
    );
}

vf_status vf_frontend_push(
    vf_frontend *frontend,
    const uint8_t *pcm,
    size_t input_samples,
    float *output,
    size_t output_capacity_frames,
    size_t *out_frames
) {
    size_t required;
    size_t index;
    size_t emitted = 0u;
    if (frontend == NULL || out_frames == NULL || input_samples > SIZE_MAX / 2u ||
        (input_samples != 0u && pcm == NULL)) {
        return VF_INVALID_ARGUMENT;
    }
    *out_frames = 0u;
    if (frontend->flushed) {
        return VF_ALREADY_FLUSHED;
    }
    required = vf_frontend_push_frame_capacity(frontend, input_samples);
    if (required == SIZE_MAX) {
        return VF_INVALID_ARGUMENT;
    }
    if (required > output_capacity_frames || (required != 0u && output == NULL)) {
        return VF_OUTPUT_TOO_SMALL;
    }
    if (input_samples != 0u && !vf_reserve(
            frontend,
            frontend->config.frame_length - frontend->pending_length
        )) {
        return VF_OUT_OF_MEMORY;
    }
    for (index = 0u; index < input_samples; ++index) {
        const uint16_t raw = (uint16_t)pcm[index * 2u] |
                             ((uint16_t)pcm[index * 2u + 1u] << 8u);
        const int32_t signed_sample = raw >= 32768u ?
                                          (int32_t)raw - 65536 : (int32_t)raw;
        const float sample = (float)signed_sample / 32768.0f;
        if (frontend->config.resampling == VF_RESAMPLING_NONE) {
            vf_add_processing_sample(frontend, sample, output, &emitted);
            ++frontend->input_samples_received;
            ++frontend->resampled_samples_emitted;
            continue;
        }
        frontend->source_history[
            frontend->input_samples_received %
            frontend->config.resample_filter_taps
        ] = sample;
        ++frontend->input_samples_received;
        while (frontend->next_source_index < frontend->input_samples_received) {
            vf_add_processing_sample(
                frontend,
                vf_resample_current(frontend),
                output,
                &emitted
            );
            vf_advance_resampler(frontend);
        }
    }
    if (emitted != required) {
        return VF_INTERNAL_ERROR;
    }
    *out_frames = emitted;
    return VF_OK;
}

vf_status vf_frontend_flush(
    vf_frontend *frontend,
    float *output,
    size_t output_capacity_frames,
    size_t *out_frames
) {
    size_t required;
    size_t emitted = 0u;
    if (frontend == NULL || out_frames == NULL) {
        return VF_INVALID_ARGUMENT;
    }
    *out_frames = 0u;
    if (frontend->flushed) {
        return VF_ALREADY_FLUSHED;
    }
    required = vf_frontend_flush_frame_capacity(frontend);
    if (required == SIZE_MAX) {
        return VF_INVALID_ARGUMENT;
    }
    if (required > output_capacity_frames || (required != 0u && output == NULL)) {
        return VF_OUTPUT_TOO_SMALL;
    }
    while (frontend->pending_length != 0u) {
        vf_emit_frame(
            frontend,
            output + emitted * frontend->config.mel_bins
        );
        vf_consume_hop(frontend);
        ++emitted;
    }
    frontend->flushed = 1;
    *out_frames = emitted;
    return VF_OK;
}

vf_status vf_frontend_reset(vf_frontend *frontend) {
    if (frontend == NULL) {
        return VF_INVALID_ARGUMENT;
    }
    frontend->pending_length = 0u;
    memset(frontend->source_history, 0, sizeof(frontend->source_history));
    frontend->input_samples_received = 0u;
    frontend->resampled_samples_emitted = 0u;
    frontend->next_source_index = 0u;
    frontend->next_phase_numerator = 0u;
    frontend->flushed = 0;
    return VF_OK;
}

void vf_frontend_destroy(vf_frontend *frontend) {
    if (frontend != NULL) {
        free(frontend->pending);
        frontend->pending = NULL;
        free(frontend);
    }
}

const char *vf_status_message(vf_status status) {
    switch (status) {
        case VF_OK:
            return "ok";
        case VF_INVALID_ARGUMENT:
            return "invalid argument";
        case VF_INVALID_MANIFEST:
            return "invalid manifest";
        case VF_OUT_OF_MEMORY:
            return "out of memory";
        case VF_OUTPUT_TOO_SMALL:
            return "output too small";
        case VF_ALREADY_FLUSHED:
            return "frontend already flushed";
        case VF_INTERNAL_ERROR:
            return "internal error";
        default:
            return "unknown status";
    }
}
