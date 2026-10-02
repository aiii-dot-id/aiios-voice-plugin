#pragma once
#include "speaker_registry.h"
#include <algorithm>
#include <chrono>
#include <deque>

namespace aii::uid {
// Transient corroboration, not another durable registry or an authorization
// store. The owner calls only for validated unknown observations against the
// SAME authoritative registry snapshot. No raw audio or tentative UUID escapes.
class ProfileAdmissions {
 public:
  using Clock=std::chrono::steady_clock;
  static constexpr size_t capacity=16;
  static constexpr size_t replay_capacity=256;
  static constexpr auto lifetime=std::chrono::minutes(10);
  void expire(Clock::time_point now=Clock::now()) {
    while(!pending_.empty() && now-pending_.front().created>=lifetime)pending_.pop_front();
    while(!seen_.empty() && now-seen_.front().second>=lifetime)seen_.pop_front();
  }
  bool corroborates(const std::string& base,const PolicyDocument& policy,
      uint64_t session,uint64_t utterance,const Sample& sample,
      Clock::time_point now=Clock::now(),Sample* corroborating=nullptr) {
    if(base_!=base) { pending_.clear();base_=base; }
    expire(now);
    if(!session || !utterance)return false; // no fabricated observation origin
    // Exact evidence replay never supplies an independent corroboration.
    for(const auto& p:seen_)if(p.first==sample.audio_sha256)return false;
    if(seen_.size()==replay_capacity)return false; // refuse, never evict an unexpired replay fence
    seen_.emplace_back(sample.audio_sha256,now);
    Snapshot candidates{policy.policy,pending_.empty()?uint64_t(0):uint64_t(1),{}};
    for(const auto& p:pending_)
      candidates.speakers.push_back({p.sample.audio_sha256,"candidate",{p.sample}});
    std::sort(candidates.speakers.begin(),candidates.speakers.end(),[](const auto& a,const auto& b){return a.id<b.id;});
    const auto decision=identify(candidates,policy.policy,sample.embedding,policy.policy.embedding_binding);
    if(decision.outcome=="known") {
      const auto p=std::find_if(pending_.begin(),pending_.end(),[&](const auto& p){return p.sample.audio_sha256==decision.speaker_id;});
      if(p==pending_.end())return false; // an unmatched decision can never authorize admission
      // Track slots of one utterance are not independent confirmations.
      if(p->session==session && p->utterance==utterance)return false;
      // The durable profile must retain both independent recordings that
      // justified admission. Otherwise corroboration disappears at publish
      // and matching falls back to the last utterance's conditions alone.
      if(corroborating)*corroborating=p->sample;
      return true;
    }
    // Ambiguity cannot corroborate, but discarding every ambiguous query
    // strands clean speech between two noisy first samples until expiry.
    // Retain this observation separately; do not merge or erase competitors.
    // A later utterance must still win against ALL remaining candidates.
    if(pending_.size()==capacity)pending_.pop_front(); // bounded transient state only
    pending_.push_back({sample,session,utterance,now});
    return false;
  }
  void clear(){pending_.clear();seen_.clear();base_.clear();}
  size_t size() const {return pending_.size();}
 private:
  struct Pending {Sample sample;uint64_t session,utterance;Clock::time_point created;};
  std::string base_;
  std::deque<Pending> pending_;
  std::deque<std::pair<std::string,Clock::time_point>> seen_;
};
}
