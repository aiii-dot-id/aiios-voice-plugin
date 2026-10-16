#pragma once
#include <cstddef>

namespace aii::voice {
// The producer can retain eight jobs behind one executing job. A final that
// cannot enter that queue is immediately followed by its unavailable verdict.
// The consumer must have room for that final too, before reading the verdict.
inline constexpr std::size_t speaker_queue_capacity = 8;
inline constexpr std::size_t speaker_pending_capacity = speaker_queue_capacity + 2;
}
