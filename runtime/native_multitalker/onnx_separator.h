#pragma once
#include "separating_recognizer.h"
#include <memory>
#include <string>

namespace aii::multitalker {
class OnnxSeparator final:public SourceSeparator {
 public:
  OnnxSeparator(const void* graph,size_t bytes,int threads,int cuda=-1,
                const std::string& profile={});
  ~OnnxSeparator();
  static constexpr size_t input_limit=80003;
  size_t maximum_samples() const override {return input_limit;}
  std::string provider() const override;
  void open() override;
  Waveforms separate(const std::vector<float>&) override;
  void cancel() noexcept override;
  std::string end_profile();
 private:
  struct Impl;
  std::unique_ptr<Impl> p_;
};
}
