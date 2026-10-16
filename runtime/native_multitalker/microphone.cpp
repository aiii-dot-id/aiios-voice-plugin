#include "microphone.h"
#include "nemotron_targets.h"
#include <algorithm>
#include <chrono>

namespace aii::multitalker {
Microphone::Microphone(const std::string& root,const float* mel,size_t count,const NemotronConfig& config,const EncoderExecution& execution)
    :hearing_(root,config.model.empty(),config.share_dormant,execution),frontend_(mel,count),mel_(mel,mel+count),refine_enabled_(config.refine_evidence){
  if(refine_enabled_ && config.model.empty())throw std::invalid_argument("refinement requires Nemotron");
#ifdef AII_NEMOTRON_DIAR
  if(!config.model.empty())nemotron_=std::make_unique<Nemotron>(config);
#else
  if(!config.model.empty())throw std::invalid_argument("Nemotron is not in this build");
#endif
}
void Microphone::reset(uint64_t epoch,bool continue_capture) {
  if(continue_capture && (!ended_ || faulted_))
    throw std::runtime_error("only a finished microphone utterance can continue");
  hearing_.reset(epoch,continue_capture);frontend_=aii::asr::Frontend(mel_.data(),mel_.size());
#ifdef AII_NEMOTRON_DIAR
  if(nemotron_)nemotron_->reset(continue_capture);
#endif
  epoch_=epoch;position_=0;activity_frames_=target_frames_=0;ended_=faulted_=false;
  cancelled_.store(false);refinement_={};refine_attempted_=false;refinement_seconds_=0;
  refinement_status_=refine_enabled_?"pending":"disabled";
}
std::vector<MicrophoneUpdate> Microphone::accept(const float* pcm,size_t count) {
  if(!epoch_ || ended_ || faulted_)throw std::runtime_error("microphone is not open");
  try {
    if(cancelled_.load())throw std::runtime_error("microphone cancelled");
    if(refine_enabled_)refinement_.append(pcm,count);
#ifdef AII_NEMOTRON_DIAR
    if(nemotron_)nemotron_->push(pcm,count);
#endif
    frontend_.accept(pcm,count);return consume(false);
  }
  catch(...) { refinement_.clear();faulted_=true;throw; }
}
std::vector<MicrophoneUpdate> Microphone::finish(const std::function<void()>& completed) {
  if(!epoch_ || ended_ || faulted_)throw std::runtime_error("microphone is not open");
  try {
    ended_=true;
#ifdef AII_NEMOTRON_DIAR
    if(nemotron_) {nemotron_->finish();if(completed)completed();}
#else
    (void)completed;
#endif
    if(!frontend_.samples())return {};
    frontend_.finish();auto updates=consume(true);
    if(refine_enabled_)refinement_.seal(activity_frames_);
    return updates;
  } catch(...) {refinement_.clear();faulted_=true;throw;}
}
std::vector<MicrophoneUpdate> Microphone::consume(bool final) {
  std::vector<MicrophoneUpdate> updates;
  const auto ready=frontend_.frames_ready();
  bool v3=false;
#ifdef AII_NEMOTRON_DIAR
  v3=bool(nemotron_);
#endif
  while(position_<ready) {
    const size_t shift=(position_||v3)?112:105,remaining=ready-position_;
    // Hold the full last chunk until its terminal status is known. This one
    // feature-frame lookahead preserves keep-all final encoder semantics.
    if(!final && remaining<=shift)break;
    const size_t take=std::min(shift,remaining),cache=(position_||v3)?9:0;
    if(take<((position_||v3)?8:1))break; // pinned causal subsampling geometry
    const bool last=final && remaining<=shift;
#ifdef AII_NEMOTRON_DIAR
    if(nemotron_) {
      const auto first=target_frames_*8;
      const auto available=nemotron_->frames();
      // The causal graph owns output geometry; this is a conservative wait
      // bound only, never a fabricated output-frame count.
      const size_t maximum_masks=(take+cache)/8;
      if(!final && first+maximum_masks*8>available)break;
      // NeMo FeatureStacking composition pads the first ASR cache and drops
      // two preencoded frames on EVERY chunk. The legacy first 105-frame
      // window shifts V3's masks by 70 ms and damages cold-overlap words.
      auto features=position_?frontend_.frames(position_-cache,take+cache):std::vector<float>(cache*128,0);
      if(!position_) {
        auto first_features=frontend_.frames(0,take);
        features.insert(features.end(),first_features.begin(),first_features.end());
      }
      auto shared=hearing_.preencode(features.data(),take+cache,take+cache,2);
      const auto masks=shared.frames,wanted=masks*8;
      if(!final && first+wanted>available)throw std::runtime_error("Nemotron target lookahead unavailable");
      const size_t n=first<available?std::min<uint64_t>(wanted,available-first):0;
      const auto fine=n?nemotron_->range(first,n):std::vector<float>{};
      const auto targets=nemotron_targets(fine,masks);
      if(refine_enabled_)refinement_.targets(targets);
      auto tracks=hearing_.push_conditioned(epoch_,shared,last,targets);
      updates.push_back({position_*160,std::min((position_+take)*160,frontend_.samples()),std::move(tracks),activity_frames_,{},8,160});
      // UID consumes every native frame exactly once, not averaged masks.
      const auto evidence_end=last?available:std::min<uint64_t>(first+wanted,available);
      if(evidence_end>activity_frames_) {
        updates.back().activity=nemotron_->range(activity_frames_,evidence_end-activity_frames_);
        activity_frames_=evidence_end;
      }
      target_frames_+=masks;
      nemotron_->discard_before(std::min(activity_frames_,target_frames_*8));
    } else
#endif
    {
    auto features=frontend_.frames(position_-cache,take+cache);
    auto tracks=hearing_.push(epoch_,features.data(),take+cache,take+cache,position_?2:0,last);
    const auto& activity=hearing_.activity();
    updates.push_back({position_*160,std::min((position_+take)*160,frontend_.samples()),std::move(tracks),activity_frames_,activity});
    activity_frames_+=activity.size()/4;
    }
    position_+=shift;
    const size_t next=position_>9?(position_-9)*160:0;
    const size_t retain=next>257?next-257:0;
    frontend_.discard_before(std::min(retain,frontend_.samples()));
  }
  if(final) {
    // The utterance can end on a tail too short for another chunk, and then
    // no chunk was the last one: release what a term being weighed withheld.
    auto rest=hearing_.finish(epoch_);
    if(!rest.empty())updates.push_back({frontend_.samples(),frontend_.samples(),std::move(rest),activity_frames_,{},
                                        v3?size_t(8):size_t(4),v3?uint64_t(160):uint64_t(1280)});
  }
#ifdef AII_NEMOTRON_DIAR
  if(final && nemotron_ && activity_frames_<nemotron_->frames()) {
    // A sub-convolution tail still has real diarization evidence, even when
    // it cannot produce another ASR frame. Do not invent or discard samples.
    const auto end=nemotron_->frames();
    updates.push_back({0,frontend_.samples(),{},activity_frames_,
                      nemotron_->range(activity_frames_,end-activity_frames_),8,160});
    activity_frames_=end;nemotron_->discard_before(end);
  }
#endif
  return updates;
}
std::optional<RefinedEvidence> Microphone::refine_evidence(bool needed,const std::function<void()>& completed) {
  if(faulted_ || cancelled_.load()) {
    refinement_.clear();throw std::runtime_error("refinement cancelled or faulted");
  }
  if(!ended_)throw std::runtime_error("refinement requires finished hearing");
  if(!refine_enabled_)return std::nullopt;
  if(refine_attempted_)throw std::runtime_error("refinement already attempted");
  refine_attempted_=true;
  if(!needed){refinement_.clear();refinement_status_="not_needed";return std::nullopt;}
  if(!refinement_.available()){refinement_status_="unavailable";return std::nullopt;}
#ifdef AII_NEMOTRON_DIAR
  const auto started=std::chrono::steady_clock::now();
  try {
    // The replay runs on a copy of the live stream: the next utterance
    // continues from the speaker memory the live pass left, whether the
    // replay is accepted, refused, cancelled or faulted.
    const auto fine=nemotron_->replay(refinement_.pcm(),refinement_.frames(),
        [this]{return cancelled_.load();},completed);
    std::optional<RefinedEvidence> result;
    if(refinement_.matches(fine)){result=refinement_.evidence(fine);refinement_status_="identical_masks";}
    else refinement_status_="changed_masks";
    refinement_.clear();
    refinement_seconds_=std::chrono::duration<double>(std::chrono::steady_clock::now()-started).count();
    if(cancelled_.load())throw std::runtime_error("refinement cancelled");
    return result;
  } catch(...) {refinement_.clear();faulted_=true;throw;}
#else
  (void)completed;
  throw std::runtime_error("refinement requires Nemotron build");
#endif
}
}
