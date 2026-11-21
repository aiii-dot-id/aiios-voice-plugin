#pragma once
#include "separating_recognizer.h"
#include <memory>
#include <string>

namespace aii::multitalker {
class OnnxSeparatorModel {
 public:
  OnnxSeparatorModel(const void* graph,size_t bytes,int threads,int cuda=-1,const std::string& profile={});
  ~OnnxSeparatorModel();
  OnnxSeparatorModel(const OnnxSeparatorModel&)=delete;
  OnnxSeparatorModel& operator=(const OnnxSeparatorModel&)=delete;
  struct Impl;
  Impl& impl() const {return *p_;}
 private:
  std::unique_ptr<Impl> p_;
};
class OnnxSeparator final:public SourceSeparator {
 public:
  OnnxSeparator(const void* graph,size_t bytes,int threads,int cuda=-1,
                const std::string& profile={});
  explicit OnnxSeparator(std::shared_ptr<OnnxSeparatorModel>);
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
