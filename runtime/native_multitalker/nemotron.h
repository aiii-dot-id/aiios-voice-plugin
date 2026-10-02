#pragma once
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>

namespace aii::multitalker {
struct NemotronConfig {std::string model;int gpu=-1;bool share_dormant=true;bool refine_evidence=false;};
// Single inference owner. The containing recognizer checks concurrent cancel
// before and after each bounded native call; it never closes a running stream.
class Nemotron {
 public:
  explicit Nemotron(const NemotronConfig&);
  ~Nemotron();
  void reset(bool continue_capture);
  void push(const float*,size_t);
  void finish();
  uint64_t frames() const;
  uint64_t retained_frames() const;
  void discard_before(uint64_t frame);
  std::vector<float> range(uint64_t first,size_t count) const;
  // A deep copy of this stream's state (speaker cache, FIFO, buffers, clocks)
  // sharing this model. It must not outlive this object and is used only
  // sequentially, on this object's inference thread. Feeding it never
  // changes this stream.
  std::unique_ptr<Nemotron> copy() const;
  // Bounded evidence replay of a finished utterance's PCM, in calls of at
  // most one second. It runs on a copy taken where the next utterance would
  // start, so its starting memory is the live pass's, and neither an accepted
  // nor a refused replay moves the memory the next utterance continues from.
  // The copy is closed on every path. Returns all native probabilities, or
  // none when the replay's frame extent differs from `frames`.
  std::vector<float> replay(const std::vector<float>& pcm,uint64_t frames,
                            const std::function<bool()>& cancelled,
                            const std::function<void()>& completed) const;
 private:
  struct Impl;
  explicit Nemotron(std::unique_ptr<Impl>);
  std::unique_ptr<Impl> p_;
};
}
