#pragma once
#include <cstddef>
#include <memory>
#include <stdexcept>
#include <vector>

namespace aii::voice::wire {
// Single pump-owner scratch. A pending output always owns a separate, exact
// copy: a later poll or a shorter next-session reply cannot change its bytes.
// The empty-poll path must neither allocate nor clear this 480 KB buffer.
template<class Allocator = std::allocator<float>> class AudioScratch {
  std::vector<float, Allocator> storage_;
public:
  static constexpr size_t capacity = 120000;
  AudioScratch() : storage_(capacity) {}
  float* data() noexcept { return storage_.data(); }
  size_t size() const noexcept { return storage_.size(); }
  std::vector<float> copy(size_t count) const {
    if (count > storage_.size())
      throw std::runtime_error("native audio exceeds scratch capacity");
    return {storage_.begin(), storage_.begin() + count};
  }
};
} // namespace aii::voice::wire
