#pragma once
#include "onnx_backend.h"
#include "diar_cache.h"

namespace aii::multitalker {
struct TrackUpdate {
  uint32_t track;
  std::vector<Token> tokens;
};
// One capture, up to eight speaker-conditioned recognizers. Inputs are
// time-major 128-bin microphone features, not masks from a separate caller.
class Hearing {
 public:
  explicit Hearing(const std::string& graph_root,bool legacy_diarization=true,bool share_dormant=true,const EncoderExecution& execution={});
  // ASR state is utterance-local. Speaker cache is capture/session-local.
  void reset(uint64_t epoch,bool continue_capture=false);
  std::vector<TrackUpdate> push(uint64_t epoch,const float* features,size_t frames,
                               size_t valid,size_t drop,bool final_chunk);
  // Internal model composition only: inferred, 80-ms, frame-major targets.
  // All eight Nemotron channels participate, including competing speakers.
  CaptureEmbeddings preencode(const float* features,size_t frames,size_t valid,size_t drop);
  std::vector<TrackUpdate> push_conditioned(uint64_t epoch,const CaptureEmbeddings& shared,
                               bool final_chunk,const std::vector<float>& targets);
  // Nothing more will be heard in this epoch. An utterance can end on a
  // tail too short to make another chunk, so no push carried final_chunk:
  // this releases what a term still being weighed withheld. After a final
  // chunk it returns nothing.
  std::vector<TrackUpdate> finish(uint64_t epoch);
  void cancel() noexcept;
  // Terms the decoder leans toward, from the next reset on (decoder.h).
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
