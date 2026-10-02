#include "hearing.h"
#include <algorithm>

namespace aii::multitalker {
Hearing::Hearing(const std::string& root,bool legacy,bool share,const EncoderExecution& execution):capture_(root,legacy),encoder_(root,execution),backend_(root),decoder_(backend_),share_dormant_(share){}
void Hearing::reset(uint64_t epoch,bool continue_capture) {
  if(!epoch || epoch<=epoch_)throw std::invalid_argument("hearing epoch must advance");
  if(continue_capture && (faulted_ || cancelled_.load()))
    throw std::runtime_error("faulted hearing cannot continue a capture");
  capture_.reopen(); encoder_.reset(epoch); decoder_.reset(epoch);
  if(!continue_capture)diar_={};
  recent_.clear(); activity_.clear(); clocks_={}; epoch_=epoch;faulted_=ended_=false;cancelled_.store(false);
  activated_={};dormant_tokens_.clear();
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
CaptureEmbeddings Hearing::preencode(const float* features,size_t frames,size_t valid,size_t drop) {
  if(faulted_ || ended_ || cancelled_.load())throw std::runtime_error("hearing epoch retired");
  try {return capture_.preencode(features,frames,valid,drop,false);}
  catch(...) {faulted_=true;throw;}
}
std::vector<TrackUpdate> Hearing::push_conditioned(uint64_t epoch,const CaptureEmbeddings& shared,
    bool final_chunk,const std::vector<float>& targets) {
  if(!epoch || epoch!=epoch_)throw std::invalid_argument("hearing epoch differs");
  if(faulted_ || ended_ || cancelled_.load())throw std::runtime_error("hearing epoch retired");
  try {
    if(targets.size()!=shared.frames*track_count)throw std::invalid_argument("Nemotron ASR target geometry");
    std::vector<TrackUpdate> updates;
    // All never-active tracks have exactly the same zero foreground and
    // union-of-speakers background. Compute that identical state once, then
    // copy it BEFORE the first differing mask. This is memoization, not gating.
    if(share_dormant_)for(uint32_t track=0;track<track_count;++track)if(!activated_[track]) {
      bool active=false;
      for(size_t f=0;f<shared.frames;++f)active=active || targets[f*track_count+track]>.5f;
      if(active) {
        encoder_.clone_track(epoch,dormant_track,track);decoder_.clone_track(epoch,dormant_track,track);
        clocks_[track]=clocks_[dormant_track];activated_[track]=true;
        if(!dormant_tokens_.empty())updates.push_back({track,dormant_tokens_});
      }
    }
    const bool dormant=share_dormant_ && std::find(activated_.begin(),activated_.end(),false)!=activated_.end();
    for(uint32_t track=0;track<track_count+(dormant?1:0);++track) {
      if(share_dormant_ && track<track_count && !activated_[track])continue;
      if(cancelled_.load())throw std::runtime_error("hearing cancelled");
      std::vector<float> foreground(shared.frames,0),background(shared.frames,0);
      for(size_t f=0;f<shared.frames;++f) {
        foreground[f]=track<track_count && targets[f*track_count+track]>.5f?1.f:0.f;
        for(size_t other=0;other<track_count;++other)
          if(other!=track && targets[f*track_count+other]>.5f)background[f]=1;
      }
      // No cache gating: a speaker discovered during overlap still needs the
      // preceding acoustic context. Skipping those calls loses opening words.
      auto encoded=encoder_.push(epoch,track,shared.values.data(),shared.frames,shared.valid,
                                foreground.data(),background.data(),final_chunk);
      if(cancelled_.load())throw std::runtime_error("hearing cancelled");
      const auto count=encoded.size()/encoder_width;
      auto tokens=decoder_.push(epoch,track,clocks_[track],encoded.data(),count);
      clocks_[track]+=count;
      if(track==dormant_track) {
        if(tokens.size()>65536-dormant_tokens_.size())throw std::runtime_error("dormant token extent");
        dormant_tokens_.insert(dormant_tokens_.end(),tokens.begin(),tokens.end());
        if(final_chunk && !dormant_tokens_.empty())for(uint32_t t=0;t<track_count;++t)
          if(!activated_[t])updates.push_back({t,dormant_tokens_});
      } else updates.push_back({track,std::move(tokens)});
    }
    ended_=final_chunk;return updates;
  } catch(...) {faulted_=true;throw;}
}
}
