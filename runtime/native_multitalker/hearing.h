#pragma once
#include "onnx_backend.h"
#include "diar_cache.h"

namespace aii::multitalker {
struct TrackUpdate {
  uint32_t track;
  std::vector<Token> tokens;
};
// One capture, four independent speaker-conditioned recognizers. Inputs are
// time-major 128-bin microphone features, not masks from a separate caller.
class Hearing {
 public:
  explicit Hearing(const std::string& graph_root);
  void reset(uint64_t epoch);
  std::vector<TrackUpdate> push(uint64_t epoch,const float* features,size_t frames,
                               size_t valid,size_t drop,bool final_chunk);
  void cancel() noexcept;
  size_t retained_diarization_frames() const { return diar_.frames(); }
 private:
  OnnxCapture capture_;
  OnnxEncoder encoder_;
  OnnxBackend backend_;
  Decoder decoder_;
  DiarCache diar_;
  std::vector<float> recent_;
  std::array<uint64_t,4> clocks_{};
  uint64_t epoch_=0;
  bool faulted_=false,ended_=false;
  std::atomic<bool> cancelled_{false};
};
}
