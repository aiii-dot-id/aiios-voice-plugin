#pragma once
#include "onnx_backend.h"
#include "diar_cache.h"

namespace aii::multitalker {
struct TrackUpdate {
  uint32_t track;
  std::vector<Token> tokens;
};
class Hearing {
 public:
  explicit Hearing(const std::string& graph_root,bool legacy_diarization=true,bool share_dormant=true,const EncoderExecution& execution={});
  void reset(uint64_t epoch,bool continue_capture=false);
  std::vector<TrackUpdate> push(uint64_t epoch,const float* features,size_t frames,
                               size_t valid,size_t drop,bool final_chunk);
  CaptureEmbeddings preencode(const float* features,size_t frames,size_t valid,size_t drop);
  std::vector<TrackUpdate> push_conditioned(uint64_t epoch,const CaptureEmbeddings& shared,
                               bool final_chunk,const std::vector<float>& targets);
  std::vector<TrackUpdate> finish(uint64_t epoch);
  void cancel() noexcept;
  void boost(std::shared_ptr<const TermBoost> terms) { decoder_.boost(std::move(terms)); }
  size_t retained_diarization_frames() const { return diar_.frames(); }
  const std::vector<float>& activity() const { return activity_; }
 private:
  OnnxCapture capture_;
  OnnxEncoder encoder_;
  OnnxBackend backend_;
  Decoder decoder_;
  DiarCache diar_;
  std::vector<float> recent_,activity_;
  std::array<uint64_t,state_count> clocks_{};
  std::array<bool,track_count> activated_{};
  std::vector<Token> dormant_tokens_;
  bool share_dormant_;
  uint64_t epoch_=0;
  bool faulted_=false,ended_=false;
  std::atomic<bool> cancelled_{false};
};
}
