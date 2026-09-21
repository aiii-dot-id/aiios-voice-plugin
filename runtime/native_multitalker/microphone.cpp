#include "microphone.h"
#include <algorithm>

namespace aii::multitalker {
Microphone::Microphone(const std::string& root,const float* mel,size_t count)
    :hearing_(root),frontend_(mel,count),mel_(mel,mel+count){}
void Microphone::reset(uint64_t epoch) {
  hearing_.reset(epoch);frontend_=aii::asr::Frontend(mel_.data(),mel_.size());
  epoch_=epoch;position_=0;ended_=faulted_=false;
}
std::vector<MicrophoneUpdate> Microphone::accept(const float* pcm,size_t count) {
  if(!epoch_ || ended_ || faulted_)throw std::runtime_error("microphone is not open");
  try { frontend_.accept(pcm,count);return consume(false); }
  catch(...) { faulted_=true;throw; }
}
std::vector<MicrophoneUpdate> Microphone::finish() {
  if(!epoch_ || ended_ || faulted_)throw std::runtime_error("microphone is not open");
  try {
    ended_=true;
    if(!frontend_.samples())return {};
    frontend_.finish();return consume(true);
  } catch(...) {faulted_=true;throw;}
}
std::vector<MicrophoneUpdate> Microphone::consume(bool final) {
  std::vector<MicrophoneUpdate> updates;
  const auto ready=frontend_.frames_ready();
  while(position_<ready) {
    const size_t shift=position_?112:105,remaining=ready-position_;
    // Hold the full last chunk until its terminal status is known. This one
    // feature-frame lookahead preserves keep-all final encoder semantics.
    if(!final && remaining<=shift)break;
    const size_t take=std::min(shift,remaining),cache=position_?9:0;
    if(take<(position_?8:1))break; // pinned causal subsampling geometry
    auto features=frontend_.frames(position_-cache,take+cache);
    const bool last=final && remaining<=shift;
    auto tracks=hearing_.push(epoch_,features.data(),take+cache,take+cache,position_?2:0,last);
    updates.push_back({position_*160,std::min((position_+take)*160,frontend_.samples()),std::move(tracks)});
    position_+=shift;
    const size_t next=position_>9?(position_-9)*160:0;
    const size_t retain=next>257?next-257:0;
    frontend_.discard_before(std::min(retain,frontend_.samples()));
  }
  return updates;
}
}
