// Exact layout-only preparation for immutable decoder upsampling weights.
#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <vector>

namespace aii_voice {
inline std::vector<float> pack_encoder_weights(const std::vector<float> & source,
        size_t input_channels, size_t output_channels, size_t kernel) {
    const size_t limit = std::numeric_limits<size_t>::max() / sizeof(float);
    if (!input_channels || !output_channels || !kernel ||
        input_channels > limit / output_channels ||
        input_channels * output_channels > limit / kernel ||
        source.size() != input_channels * output_channels * kernel) {
        throw std::runtime_error("encoder weight layout dimensions differ");
    }
    std::vector<float> packed(source.size());
    // Source [input, output, kernel] -> ggml contiguous [output, kernel, input].
    // Copy bits, including signed zeros; no precision or arithmetic change.
    for (size_t output = 0; output < output_channels; ++output) {
        for (size_t tap = 0; tap < kernel; ++tap) {
            for (size_t input = 0; input < input_channels; ++input) {
                const size_t from = (input * output_channels + output) * kernel + tap;
                const size_t to = (output * kernel + tap) * input_channels + input;
                std::memcpy(&packed[to], &source[from], sizeof(float));
            }
        }
    }
    return packed;
}
} // namespace aii_voice
