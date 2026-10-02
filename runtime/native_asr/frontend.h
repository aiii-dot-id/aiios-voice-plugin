#pragma once
#include <array>
#include <cstddef>
#include <vector>

namespace aii::asr {
// Exact Nemotron frontend. No audio devices, model discovery, or inference.
class Frontend {
 public:
  static constexpr size_t kCapacity = 16000 * 61;
  explicit Frontend(const float* mel, size_t count);
  void accept(const float* samples, size_t count);
  void finish();
  size_t frames_ready() const;
  size_t samples() const { return base_ + pcm_.size(); }
  size_t retained_samples() const { return pcm_.size(); }
  size_t first_sample() const { return base_; }
  // The stream owner releases samples only after their final overlapping
  // feature window has been consumed. Absolute frame/sample clocks never reset.
  void discard_before(size_t absolute_sample);
  bool finished() const { return finished_; }
  std::vector<float> frames(size_t first, size_t count) const;
 private:
  // Model-sized coefficients belong on the heap, not an embedding caller's
  // small Windows/mobile thread stack. The numerical order stays unchanged.
  std::vector<float> mel_;
  std::array<float, 512> window_{};
  std::vector<float> pcm_;
  size_t base_ = 0;
  bool finished_ = false;
};
}  // namespace aii::asr
