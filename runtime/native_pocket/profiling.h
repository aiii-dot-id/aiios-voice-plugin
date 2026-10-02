#pragma once
// Private diagnostic build only; no SDK control or production dependency.
#include <atomic>
#include <chrono>
#include <cstdint>

namespace native_voice_profile {
extern std::atomic<uint64_t> prepare_ns, acoustic_ns, decoder_ns, decoded_frames;
template <typename Fn>
auto measure(std::atomic<uint64_t>& counter, Fn&& fn) {
    const auto begin = std::chrono::steady_clock::now();
    auto result = fn();
    const auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now() - begin).count();
    counter.fetch_add(static_cast<uint64_t>(elapsed), std::memory_order_relaxed);
    return result;
}
}
