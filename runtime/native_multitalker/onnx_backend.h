#pragma once
#include "decoder.h"
#include <memory>
#include <string>

namespace aii::multitalker {
struct EncoderWeights;
struct EncoderExecution {
  int threads=2;
  int cuda_device=-1;
  std::string profile_prefix;
  std::shared_ptr<EncoderWeights> weights{};
};
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
