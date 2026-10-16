#pragma once
#include "speaker_evidence.h"
#include <deque>
#include <stdexcept>
#include <cmath>
#include <algorithm>
#include <utility>

namespace aii::multitalker {
struct EvidenceAudioSpan {
  EvidenceSpan span;
  std::vector<float> pcm;
  std::vector<std::pair<uint64_t,uint64_t>> regions{};
};
// Private, bounded PCM custody. No embeddings or audio reach the event wire.
// Recognition supplies activity after its lookahead; retain twelve seconds,
// plus at most ten seconds of gathered clean PCM for each of eight tracks.
// Original-clock regions preserve provenance across omitted uncertain gaps.
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
  void collect(const std::vector<EvidenceSpan>& regions) {
    if(regions.size()>128)throw std::invalid_argument("speaker evidence region count");
    for(auto span:regions) {
      if(span.track>=8 || span.start>=span.end || !span.active_samples ||
         span.active_samples>span.end-span.start || span.end-span.start>SpeakerEvidence::maximum_span ||
         span.start<last_end_[span.track])
        throw std::invalid_argument("speaker evidence region extent/order");
      last_end_[span.track]=span.end;
      // Predictions may include a padded final frame. Never copy nonexistent
      // PCM, and preserve the right-hand guard when clipping that prediction.
      if(span.end>total_) {
        const auto end=total_>SpeakerEvidence::guard?total_-SpeakerEvidence::guard:0;
        span.active_samples-=std::min(span.active_samples,span.end-end);
        span.end=end;
      }
      auto& sample=best_[span.track];
      const auto base=total_-ring_.size();
      if(span.start<base) {expired_[span.track]=true;continue;}
      if(span.start>=span.end || !span.active_samples)continue;
      // A silence-heavy prefix must not consume the whole evidence allowance.
      // Prefer this complete region when it contains more active evidence than
      // the current selection plus what can safely fit. The conservative lower
      // bound on a clipped region is unchanged; never invent activity positions.
      const auto room=SpeakerEvidence::maximum_span-sample.pcm.size();
      const auto omitted=(span.end-span.start)-std::min<uint64_t>(span.end-span.start,room);
      const auto additional=sample.regions.size()==128?0:
          span.active_samples-std::min(span.active_samples,omitted);
      if(span.active_samples>sample.span.active_samples+additional)sample={};
      if(sample.pcm.size()==SpeakerEvidence::maximum_span || sample.regions.size()==128)continue;
      const auto take=std::min<uint64_t>(span.end-span.start,SpeakerEvidence::maximum_span-sample.pcm.size());
      const auto active=span.active_samples-std::min(span.active_samples,span.end-span.start-take);
      if(!active)continue;
      if(sample.pcm.empty())sample.span={span.track,span.start,span.start,0};
      sample.pcm.insert(sample.pcm.end(),ring_.begin()+size_t(span.start-base),ring_.begin()+size_t(span.start-base+take));
      sample.regions.emplace_back(span.start,span.start+take);
      sample.span.end=span.start+take;sample.span.active_samples+=active;
    }
  }
  const EvidenceAudioSpan& track(size_t track) const {
    const auto& sample=best_.at(track);
    return sample.span.active_samples>=SpeakerEvidence::minimum_active?sample:empty_;
  }
  const EvidenceAudioSpan& attribution_track(size_t track,const SpeakerEvidence& activity) const {
    // This backend has speaker-conditioned TEXT, not separated waveforms.
    // A clean island identifies that island; it does not verify the words
    // emitted while another voice was active or possibly competing. Do not
    // let the former grant a UUID to the final. Keep the raw selection for diagnostics.
    const auto& observed=activity.activity(track);
    return observed.overlap||observed.competing_uncertain?empty_:this->track(track);
  }
  bool refinement_needed(size_t track,const SpeakerEvidence& activity) const {
    const auto& observed=activity.activity(track);
    if(observed.overlap)return false;
    if(!this->track(track).pcm.empty())return observed.competing_uncertain!=0;
    // Fragmented confident speech can lose its evidence allowance to repeated
    // boundary guards. Permit an exact-mask replay, not the fragmented PCM:
    // the replay must still satisfy every guard and the original duration floor.
    return observed.uncertain &&
        observed.active>=SpeakerEvidence::minimum_active+2*SpeakerEvidence::guard;
  }
  const char* unavailable_reason(size_t track,const SpeakerEvidence& activity) const {
    const auto& observed=activity.activity(track);
    if(!this->track(track).pcm.empty())return observed.overlap||observed.competing_uncertain?
        "speaker_track_coverage_unverified":"";
    if(expired_.at(track))return "speaker_evidence_expired";
    if(observed.overlap)return "speaker_overlap_without_isolated_evidence";
    if(observed.uncertain)return "speaker_activity_uncertain";
    return observed.active?"speaker_evidence_too_short":"speaker_activity_unavailable";
  }
  size_t retained_samples() const {
    size_t n=ring_.size();for(const auto& sample:best_)n+=sample.pcm.size();return n;
  }
 private:
  std::deque<float> ring_;
  std::array<EvidenceAudioSpan,8> best_{};
  std::array<uint64_t,8> last_end_{};
  std::array<bool,8> expired_{};
  EvidenceAudioSpan empty_{};
  uint64_t total_=0;
};
}
