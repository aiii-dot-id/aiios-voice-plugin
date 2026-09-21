#pragma once
#include "hearing.h"
#include "../native_asr/frontend.h"

namespace aii::multitalker {
struct MicrophoneUpdate {
  uint64_t start_sample=0,end_sample=0;
  std::vector<TrackUpdate> tracks;
};
// Incremental 16 kHz mono capture. Keeps only the next feature window and its
// nine-frame left context, while the recognizers own bounded speaker caches.
class Microphone {
 public:
  Microphone(const std::string& graph_root,const float* mel,size_t count);
  void reset(uint64_t epoch);
  std::vector<MicrophoneUpdate> accept(const float*,size_t);
  std::vector<MicrophoneUpdate> finish();
  void cancel() noexcept { hearing_.cancel(); }
  size_t retained_samples() const { return frontend_.retained_samples(); }
 private:
  Hearing hearing_;
  aii::asr::Frontend frontend_;
  std::vector<float> mel_;
  size_t position_=0;
  uint64_t epoch_=0;
  bool ended_=false,faulted_=false;
  std::vector<MicrophoneUpdate> consume(bool final);
};
}
