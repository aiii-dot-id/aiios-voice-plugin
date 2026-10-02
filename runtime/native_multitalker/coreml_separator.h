#pragma once
#include "separating_recognizer.h"
#include <memory>
#include <string>

namespace aii::multitalker {
// The model owner verifies and pins the complete, immutable compiled bundle
// before construction. This adapter never compiles/downloads models or reads
// model-supplied paths. stage-0.mlmodelc through stage-7.mlmodelc are required.
class CoreMLSeparator final:public SourceSeparator {
 public:
  explicit CoreMLSeparator(const std::string& compiled_root,bool cpu_only=false);
  ~CoreMLSeparator();
  size_t maximum_samples() const override {return 80003;}
  std::string provider() const override;
  void open() override; // serialized, only after previous inference has retired
  Waveforms separate(const std::vector<float>&) override;
  void cancel() noexcept override; // concurrent; never waits behind inference
 private:
  struct Impl;
  std::unique_ptr<Impl> p_;
};
}
