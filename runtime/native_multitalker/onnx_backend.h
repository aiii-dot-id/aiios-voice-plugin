#pragma once
#include "decoder.h"
#include <memory>
#include <string>

namespace aii::multitalker {
// Development composition. Distribution asset binding and accelerator
// qualification remain the responsibility of the release runtime loader.
class OnnxBackend final : public Backend {
 public:
  explicit OnnxBackend(const std::string& graph_root);
  ~OnnxBackend() override;
  Prediction predict(int64_t, const State&) override;
  int64_t classify(const float*, const Prediction&) override;
  void cancel() noexcept override;
  void reopen() override;
 private:
  struct Impl;
  std::unique_ptr<Impl> p_;
};
class OnnxEncoder {
 public:
  explicit OnnxEncoder(const std::string& graph_root);
  ~OnnxEncoder();
  void reset(uint64_t epoch);
  // Shared-capture pre-encoded frames plus inferred speaker targets. Each
  // anonymous track owns its encoder caches, even after an inactive interval.
  std::vector<float> push(uint64_t epoch, uint32_t track, const float* embeddings,
                          size_t frames, size_t valid_frames, const float* foreground,
                          const float* background, bool final_chunk);
  void cancel() noexcept;
 private:
  struct Impl;
  std::unique_ptr<Impl> p_;
};
}
