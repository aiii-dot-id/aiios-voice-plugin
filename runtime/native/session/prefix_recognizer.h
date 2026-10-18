#pragma once
#include "session.h"
#include <atomic>
#include <memory>

namespace aii::voice {
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
