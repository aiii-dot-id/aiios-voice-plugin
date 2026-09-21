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
  if(end>run.start+2*guard && active>=minimum_active+2*guard) {
    EvidenceSpan candidate{static_cast<uint32_t>(track),run.start+guard,end-guard,active-2*guard};
    auto& best=best_[track];
    if(candidate.end>candidate.start && candidate.end-candidate.start>=minimum_active &&
       candidate.active_samples>best.active_samples)best=candidate;
  }
  run={};
}
void SpeakerEvidence::push(uint64_t first,const std::vector<float>& p) {
  if(finished_ || first!=frames_ || p.empty() || p.size()%4 || p.size()>128*4 ||
     p.size()/4>std::numeric_limits<uint64_t>::max()/hop-frames_)
    throw std::invalid_argument("speaker evidence clock/extent");
  // Validate all input before mutation, including the last probability.
  for(float value:p)if(!std::isfinite(value)||value<0||value>1)
    throw std::invalid_argument("speaker evidence probability");
  for(size_t f=0;f<p.size()/4;++f,++frames_) {
    const auto start=frames_*hop,end=start+hop;
    for(size_t track=0;track<4;++track) {
      bool exclusive=true;
      for(size_t other=0;other<4;++other)if(other!=track && p[f*4+other]>.1f)exclusive=false;
      auto& run=runs_[track];
      if(!exclusive){close(track,start);continue;}
      const bool active=p[f*4+track]>=.9f;
      if(!run.open && active)run={start,end,0,0,true};
      if(!run.open)continue;
      run.end=end;if(active){run.active+=hop;run.last_active_end=end;}
      if(run.end-run.start>=maximum_span)close(track,end);
    }
  }
}
std::vector<EvidenceSpan> SpeakerEvidence::finish(uint64_t actual) {
  if(finished_ || actual>frames_*hop)throw std::invalid_argument("speaker evidence terminal extent");
  finished_=true;
  for(size_t track=0;track<4;++track)close(track,actual);
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
