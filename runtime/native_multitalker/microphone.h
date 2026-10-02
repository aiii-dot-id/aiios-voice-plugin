#pragma once
#include "hearing.h"
#include "nemotron.h"
#include "refinement.h"
#include <optional>
#include <functional>
#include "../native_asr/frontend.h"

namespace aii::multitalker {
struct MicrophoneUpdate {
  uint64_t start_sample=0,end_sample=0;
  std::vector<TrackUpdate> tracks;
  // Model-native diarizer clock (activity_hop samples, activity_tracks). This is
  // activity evidence, NOT a separated waveform or an identity assertion.
  uint64_t activity_start_frame=0;
  std::vector<float> activity;
  size_t activity_tracks=4;
  uint64_t activity_hop=1280;
};
// Incremental 16 kHz mono capture. Keeps only the next feature window and its
// nine-frame left context, while the recognizers own bounded speaker caches.
class Microphone {
 public:
  Microphone(const std::string& graph_root,const float* mel,size_t count,const NemotronConfig& config={},const EncoderExecution& execution={});
  void reset(uint64_t epoch,bool continue_capture=false);
  std::vector<MicrophoneUpdate> accept(const float*,size_t);
  std::vector<MicrophoneUpdate> finish(const std::function<void()>& completed={});
  void cancel() noexcept { cancelled_.store(true);hearing_.cancel(); }
  // Experimental, opt-in. One bounded diarization-only replay after finish.
  // A changed ASR mask retains the original evidence; no text is relabelled.
  std::optional<RefinedEvidence> refine_evidence(bool needed=true,const std::function<void()>& completed={});
  const char* refinement_status() const {return refinement_status_;}
  double refinement_seconds() const {return refinement_seconds_;}
  size_t refinement_retained_samples() const {return refinement_.pcm().size();}
  size_t retained_samples() const { return frontend_.retained_samples(); }
  size_t retained_diarization_frames() const {
#ifdef AII_NEMOTRON_DIAR
    if(nemotron_)return nemotron_->retained_frames();
#endif
    return hearing_.retained_diarization_frames();
  }
 private:
  Hearing hearing_;
#ifdef AII_NEMOTRON_DIAR
  std::unique_ptr<Nemotron> nemotron_;
#endif
  aii::asr::Frontend frontend_;
  std::vector<float> mel_;
  size_t position_=0;
  uint64_t epoch_=0,activity_frames_=0;
  uint64_t target_frames_=0;
  bool ended_=false,faulted_=false;
  bool refine_enabled_=false,refine_attempted_=false;
  std::atomic<bool> cancelled_{false};
  RefinementCapture refinement_;
  const char* refinement_status_="disabled";
  double refinement_seconds_=0;
  std::vector<MicrophoneUpdate> consume(bool final);
};
}
