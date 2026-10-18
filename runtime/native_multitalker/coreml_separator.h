#pragma once
#include "separating_recognizer.h"
#include <memory>
#include <string>

namespace aii::multitalker {
class CoreMLSeparator final:public SourceSeparator {
 public:
  explicit CoreMLSeparator(const std::string& compiled_root,bool cpu_only=false);
  ~CoreMLSeparator();
  size_t maximum_samples() const override {return 80003;}
  std::string provider() const override;
  void open() override;
  Waveforms separate(const std::vector<float>&) override;
  void cancel() noexcept override;
 private:
  struct Impl;
  std::unique_ptr<Impl> p_;
};
}
