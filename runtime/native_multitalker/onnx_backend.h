#pragma once
#include "decoder.h"
#include <memory>
#include <string>

namespace aii::multitalker {
struct EncoderWeights;
struct EncoderExecution {
  int threads=2;
  int cuda_device=-1; // -1 explicitly selects CPU.
  std::string profile_prefix; // Empty in normal operation; private diagnostics.
  // Explicit model ownership. Recurrent caches and cancellation stay private
  // to each recognizer; no process-global or path-keyed cache is involved.
  std::shared_ptr<EncoderWeights> weights{};
};
// Development composition. Distribution asset binding and accelerator
// qualification remain the responsibility of the release runtime loader.
class OnnxBackend final : public Backend {
 public:
  explicit OnnxBackend(const std::string& graph_root);
  ~OnnxBackend() override;
  Prediction predict(int64_t, const State&) override;
  int64_t classify(const float*, const Prediction&) override;
  bool scores(const float*, const Prediction&, std::array<float, blank_token + 1>&) override;
  void cancel() noexcept override;
  void reopen() override;
 private:
  struct Impl;
  std::unique_ptr<Impl> p_;
};
class OnnxEncoder {
 public:
  static std::shared_ptr<EncoderWeights> load_weights(const std::string& graph_root,
                                                      const EncoderExecution& execution);
  explicit OnnxEncoder(const std::string& graph_root,const EncoderExecution& execution={});
  ~OnnxEncoder();
  void reset(uint64_t epoch);
  void clone_track(uint64_t epoch,uint32_t source,uint32_t target);
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
struct CaptureEmbeddings {
  std::vector<float> values;
  size_t frames = 0, valid = 0;
};
// Shared microphone feature owner. Neither caller-provided speaker masks nor
// reference transcripts enter these graphs.
class OnnxCapture {
 public:
  explicit OnnxCapture(const std::string& graph_root,bool legacy_diarization=true);
  ~OnnxCapture();
  CaptureEmbeddings preencode(const float* features, size_t frames,
                              size_t valid, size_t drop, bool diarization);
  std::vector<float> diarize(const std::vector<float>& embeddings);
  void cancel() noexcept;
  void reopen();
 private:
  struct Impl;
  std::unique_ptr<Impl> p_;
};
}
