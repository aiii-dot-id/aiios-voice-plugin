#pragma once
#include <cstdint>

namespace aii::voice {
inline constexpr uint32_t default_capture_limit_minutes = 30;
inline constexpr uint64_t capture_samples(uint32_t minutes) {
  return uint64_t(minutes) * 60 * 16000;
}
inline constexpr uint64_t input_clock_max = (uint64_t{1} << 53) - 512;
}
