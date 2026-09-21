#include "hearing.h"
#include <algorithm>

namespace aii::multitalker {
Hearing::Hearing(const std::string& root):capture_(root),encoder_(root),backend_(root),decoder_(backend_){}
void Hearing::reset(uint64_t epoch) {
  if(!epoch || epoch<=epoch_)throw std::invalid_argument("hearing epoch must advance");
  capture_.reopen(); encoder_.reset(epoch); decoder_.reset(epoch);
  diar_={}; recent_.clear(); activity_.clear(); clocks_={}; epoch_=epoch;faulted_=ended_=false;cancelled_.store(false);
}
void Hearing::cancel() noexcept {
  cancelled_.store(true);capture_.cancel();encoder_.cancel();decoder_.cancel();
}
std::vector<TrackUpdate> Hearing::push(uint64_t epoch,const float* features,size_t frames,
                                      size_t valid,size_t drop,bool final_chunk) {
  if(!epoch || epoch!=epoch_)throw std::invalid_argument("hearing epoch differs");
  if(faulted_ || ended_ || cancelled_.load())throw std::runtime_error("hearing epoch retired");
  try {
    // This pinned diarization encoder uses the same cached feature view and
    // subsampling as ASR. FeatureStacking models require a different contract.
    auto diar_chunk=capture_.preencode(features,frames,valid,drop,true);
    auto probabilities=capture_.diarize(diar_.input(diar_chunk.values));
    auto current=diar_.update(diar_chunk.values,probabilities);
    activity_=current;
    recent_.insert(recent_.end(),current.begin(),current.end());
    if(recent_.size()>28*4)recent_.erase(recent_.begin(),recent_.end()-28*4);
    std::array<bool,4> active{};
    for(size_t i=0;i<recent_.size();++i)if(recent_[i]>.5f)active[i%4]=true;
    if(cancelled_.load())throw std::runtime_error("hearing cancelled");
    auto shared=capture_.preencode(features,frames,valid,drop,false);
    const size_t mask_frames=std::min(size_t(14),recent_.size()/4);
    const auto* masks=recent_.data()+recent_.size()-mask_frames*4;
    std::vector<TrackUpdate> updates;
    for(uint32_t track=0;track<4;++track)if(active[track]) {
      if(cancelled_.load())throw std::runtime_error("hearing cancelled");
      std::vector<float> foreground(shared.frames,1),background(shared.frames,0);
      const size_t copied=std::min(mask_frames,shared.frames);
      for(size_t i=0;i<copied;++i) {
        const auto at=(mask_frames-copied+i)*4;
        const size_t dest=shared.frames-copied+i;
        foreground[dest]=masks[at+track]>.5f?1.f:0.f;
        for(size_t other=0;other<4;++other)
          if(other!=track && active[other] && masks[at+other]>.5f)background[dest]=1;
      }
      auto encoded=encoder_.push(epoch,track,shared.values.data(),shared.frames,shared.valid,
                                 foreground.data(),background.data(),final_chunk);
      if(cancelled_.load())throw std::runtime_error("hearing cancelled");
      const size_t count=encoded.size()/encoder_width;
      auto tokens=decoder_.push(epoch,track,clocks_[track],encoded.data(),count);
      clocks_[track]+=count;updates.push_back({track,std::move(tokens)});
    }
    ended_=final_chunk;
    return updates;
  } catch(...) { faulted_=true;throw; }
}
}
