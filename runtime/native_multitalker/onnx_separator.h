#pragma once
#include "separating_recognizer.h"
#include <memory>
#include <string>

namespace aii::multitalker {
// Model owner supplies byte-verified, self-contained ONNX data. No filesystem
// paths from a graph or new public SDK control enter this composition.
class OnnxSeparator final:public SourceSeparator {
 public:
  OnnxSeparator(const void* graph,size_t bytes,int threads,int cuda=-1,
                const std::string& profile={});
  ~OnnxSeparator();
  // Qualified resident separation window at 16 kHz. Longer mixed turns keep
  // the recognizer's complete unresolved records, never a cropped substitute.
  static constexpr size_t input_limit=80003;
  size_t maximum_samples() const override {return input_limit;}
  std::string provider() const override;
  void open() override; // serialized, after the previous call has retired
  Waveforms separate(const std::vector<float>&) override;
  void cancel() noexcept override; // concurrent, never waits behind inference
  std::string end_profile(); // serialized after inference
 private:
  struct Impl;
  std::unique_ptr<Impl> p_;
};
}
