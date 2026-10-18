#pragma once
#include <cstddef>

namespace aii::voice {
inline constexpr std::size_t speaker_queue_capacity = 8;
inline constexpr std::size_t speaker_pending_capacity = speaker_queue_capacity + 2;
}
