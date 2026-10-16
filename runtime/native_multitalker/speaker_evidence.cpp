#include "speaker_evidence.h"
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace aii::multitalker {
void SpeakerEvidence::close(size_t track,uint64_t actual) {
  auto& run=runs_[track];
  if(!run.open)return;
  // Remove boundary context conservatively from BOTH elapsed and active time.
  // Do not retain trailing silence until another speaker crosses threshold:
  // that delay can contain the other speaker's opening words.
  const auto end=std::min(run.last_active_end,actual);
  const auto active=run.active-std::min(run.active,run.last_active_end-end);
  if(end>run.start+2*guard && active>2*guard) {
    EvidenceSpan candidate{static_cast<uint32_t>(track),run.start+guard,end-guard,active-2*guard};
    if(regions_.size()>=128)throw std::runtime_error("speaker evidence region bound");
    regions_.push_back(candidate);
    auto& best=best_[track];
    if(candidate.end>candidate.start && candidate.end-candidate.start>=minimum_active &&
       candidate.active_samples>=minimum_active && candidate.active_samples>best.active_samples)best=candidate;
  }
  run={};
}
void SpeakerEvidence::push(uint64_t first,const std::vector<float>& p) {
  if(finished_ || first!=frames_ || p.empty() || p.size()%channels_ || p.size()>1024*channels_ ||
     p.size()/channels_>std::numeric_limits<uint64_t>::max()/cadence_-frames_)
    throw std::invalid_argument("speaker evidence clock/extent");
  // Validate all input before mutation, including the last probability.
  for(float value:p)if(!std::isfinite(value)||value<0||value>1)
    throw std::invalid_argument("speaker evidence probability");
  regions_.clear();
  for(size_t f=0;f<p.size()/channels_;++f,++frames_) {
    const auto start=frames_*cadence_,end=start+cadence_;
    for(size_t track=0;track<channels_;++track) {
      bool exclusive=true, competing=false;
      for(size_t other=0;other<channels_;++other)if(other!=track) {
        if(p[f*channels_+other]>.1f)exclusive=false;
        if(p[f*channels_+other]>=.9f)competing=true;
      }
      const bool active=p[f*channels_+track]>=.9f;
      auto& observed=activity_[track];
      if(active) {
        observed.active+=cadence_;
        if(competing)observed.overlap+=cadence_;
        else if(!exclusive) {
          observed.uncertain+=cadence_;
          observed.competing_uncertain+=cadence_;
        }
      } else if(p[f*channels_+track]>.1f) {
        observed.uncertain+=cadence_;
        if(!exclusive)observed.competing_uncertain+=cadence_;
      }
      auto& run=runs_[track];
      if(!exclusive){close(track,start);continue;}
      // Only confidently inactive frames may bridge a pause. An uncertain
      // own-track prediction is not silence and must not enter a voiceprint.
      if(!active && p[f*channels_+track]>.1f){close(track,start);continue;}
      if(!run.open && active)run={start,end,0,0,true};
      if(!run.open)continue;
      run.end=end;if(active){run.active+=cadence_;run.last_active_end=end;}
      if(run.end-run.start>=maximum_span)close(track,end);
    }
  }
}
std::vector<EvidenceSpan> SpeakerEvidence::finish(uint64_t actual) {
  // The final feature tail may be shorter than one diarizer prediction hop.
  // It has no activity evidence and must never extend a clean voiceprint, but
  // it is still valid microphone audio and cannot fault the final transcript.
  if(finished_ || (actual>frames_*cadence_ && actual-frames_*cadence_>=cadence_))
    throw std::invalid_argument("speaker evidence terminal extent");
  regions_.clear();
  finished_=true;
  for(size_t track=0;track<channels_;++track)close(track,actual);
  std::vector<EvidenceSpan> result;
  for(auto span:best_) {
    // Earlier emitted predictions can include right-edge subsampling padding.
    if(span.end>actual){span.active_samples-=std::min(span.active_samples,span.end-actual);span.end=actual;}
    if(span.end>span.start && span.end-span.start>=minimum_active)result.push_back(span);
  }
  return result;
}
size_t SpeakerEvidence::retained_spans() const {
  size_t n=0;for(const auto& span:best_)n+=span.end>span.start;return n;
}
std::vector<EvidenceSpan> SpeakerEvidence::spans() const {
  std::vector<EvidenceSpan> result;
  for(const auto& span:best_)if(span.end>span.start)result.push_back(span);
  return result;
}
}
