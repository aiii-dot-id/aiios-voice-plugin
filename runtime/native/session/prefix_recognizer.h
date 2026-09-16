#pragma once
#include "session.h"
#include <atomic>
#include <memory>

namespace aii::voice {
// Private model adapter, not a second host/SDK protocol. All decoder methods,
// including destruction, run on one owned inference thread. A backend must
// check cancelled before publishing and between interruptible inference steps;
// this does not promise preemption of an executing accelerator kernel.
struct PrefixDecoder {
  virtual ~PrefixDecoder() = default;
  virtual void open() = 0;
  virtual std::string decode(const std::vector<float>&,
                             const std::atomic<bool>& cancelled) = 0;
};
struct PrefixLimits {
  size_t minimum_samples;
  size_t partial_stride_samples;
  size_t maximum_samples;
};
// Push retains exact PCM and never executes or waits for model inference.
// One in-flight snapshot and one coalesced latest request are bounded by the
// model's explicit capacity. Finish requires the exact final snapshot; a stale
// partial is never substituted. Capacity exhaustion faults, never truncates.
// reset() waits for decoder retirement; cancel() only signals cancellation.
class PrefixRecognizer final : public Recognizer {
 public:
  PrefixRecognizer(std::unique_ptr<PrefixDecoder>, PrefixLimits);
  ~PrefixRecognizer() override;
  void open() override;
  void begin() override;
  std::string push(const float*, size_t) override;
  std::string finish() override;
  void reset() override;
  void cancel() noexcept override;
 private:
  struct Impl;
  std::unique_ptr<Impl> p_;
};
}
