#pragma once
#include <array>
#include <vector>
#include <cstddef>

namespace aii::multitalker {
// One capture owner. Mirrors the pinned Sortformer inference cache algorithm;
// no transcript, enrolled name or ground-truth speaker mask enters this state.
class DiarCache {
 public:
  static constexpr size_t width=512, speakers=4, cache_limit=188, fifo_limit=188;
  std::vector<float> input(const std::vector<float>& chunk) const;
  std::vector<float> update(const std::vector<float>& chunk,
                            const std::vector<float>& predictions);
  size_t frames() const { return (cache_.size()+fifo_.size())/width; }
  size_t compressions() const { return compressions_; }
 private:
  std::vector<float> cache_, fifo_, cache_probs_;
  std::array<float,width> silence_{};
  double silent_frames_=0;
  bool compressed_=false;
  size_t compressions_=0;
  void compress();
};
}
