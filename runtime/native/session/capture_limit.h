#pragma once
#include <cstdint>

namespace aii::voice {
// Audio-clock duration, including silence; not wall time or a VAD turn pause.
// The representation bound is not a hardware/model safety requirement.
inline constexpr uint32_t default_capture_limit_minutes = 30;
inline constexpr uint64_t capture_samples(uint32_t minutes) {
  return uint64_t(minutes) * 60 * 16000;
}
// Leave room for block padding while keeping wire clocks exact in JSON numbers.
inline constexpr uint64_t input_clock_max = (uint64_t{1} << 53) - 512;
}
