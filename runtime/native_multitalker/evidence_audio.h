#pragma once
#include "speaker_evidence.h"
#include <deque>
#include <stdexcept>
#include <cmath>

namespace aii::multitalker {
struct EvidenceAudioSpan { EvidenceSpan span; std::vector<float> pcm; };
// Private, bounded PCM custody. No embeddings or audio reach the event wire.
// Recognition supplies activity after its lookahead; retain twelve seconds,
// plus at most one ten-second selected sample for each of four tracks.
class EvidenceAudio {
 public:
  static constexpr size_t capacity=192000, maximum_chunk=16000;
  void append(const float* pcm,size_t count) {
    if(!pcm || !count || count>maximum_chunk || count>UINT64_MAX-total_)
      throw std::invalid_argument("speaker evidence PCM extent");
    for(size_t i=0;i<count;++i)if(!std::isfinite(pcm[i]) || pcm[i]<-1 || pcm[i]>1)
      throw std::invalid_argument("speaker evidence PCM range");
    for(size_t i=0;i<count;++i)ring_.push_back(pcm[i]);
    total_+=count;
    while(ring_.size()>capacity)ring_.pop_front();
  }
  void select(const std::vector<EvidenceSpan>& spans) {
    for(const auto& span:spans) {
      if(span.track>=4 || span.start>=span.end || span.end>total_ ||
         span.end-span.start>SpeakerEvidence::maximum_span)
        throw std::invalid_argument("speaker evidence selection extent");
      auto& best=best_[span.track];
      if(span.active_samples<=best.span.active_samples)continue;
      const auto base=total_-ring_.size();
      // A late prediction cannot reconstruct evicted audio or substitute a
      // different span. Keep any earlier valid selection, otherwise unknown.
      if(span.start<base)continue;
      best.span=span;
      best.pcm.assign(ring_.begin()+size_t(span.start-base),ring_.begin()+size_t(span.end-base));
    }
  }
  const EvidenceAudioSpan& track(size_t track) const {return best_.at(track);}
  size_t retained_samples() const {
    size_t n=ring_.size();for(const auto& sample:best_)n+=sample.pcm.size();return n;
  }
 private:
  std::deque<float> ring_;
  std::array<EvidenceAudioSpan,4> best_{};
  uint64_t total_=0;
};
}
