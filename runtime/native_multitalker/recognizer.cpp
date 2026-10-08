#include "recognizer.h"
#include <algorithm>
#include <limits>

namespace aii::multitalker {
Recognizer::Recognizer(const std::string& root,const float* mel,size_t count,std::vector<std::string> vocab,const NemotronConfig& config,const EncoderExecution& execution)
    :microphone_(root,mel,count,config,execution),vocabulary_(std::move(vocab)),nemotron_(!config.model.empty()),encoder_cuda_(execution.cuda_device>=0) {
  if(vocabulary_.size()!=1024)throw std::invalid_argument("multitalker vocabulary census");
  for(auto& word:vocabulary_) {
    if(word.size()>1024)throw std::invalid_argument("multitalker vocabulary token bound");
    size_t at=0;
    while((at=word.find("\xe2\x96\x81",at))!=std::string::npos)word.replace(at++,3," ");
  }
}
std::string Recognizer::execution_info() const {
  return std::string("{\"encoder_provider\":\"")+(encoder_cuda_?"CUDAExecutionProvider":"CPUExecutionProvider")+
    "\",\"diarization_provider\":\""+(nemotron_?"NeMo-Speech.cpp":"CPUExecutionProvider")+
    "\",\"speaker_conditioned\":true,\"hardware_execution_verified\":false}";
}
size_t Recognizer::prefer(const std::vector<std::string>& terms) {
  if(active_)throw std::runtime_error("previous hearing has not retired");
  // Each term is tried alone: one the pieces cannot spell, one that is not a
  // short phrase, or one already listed is passed over and the rest stand.
  std::vector<std::string> kept,seen;
  for(const auto& term:terms) {
    if(kept.size()==TermBoost::max_terms)break;
    try {
      const auto normal=TermBoost::normal(term);
      if(std::find(seen.begin(),seen.end(),normal)!=seen.end())continue;
      TermBoost alone(vocabulary_,{term},TermBoost::default_margin,TermBoost::default_budget);
      kept.push_back(term);seen.push_back(normal);
    } catch(const std::invalid_argument&) {}
  }
  microphone_.boost(kept.empty()?nullptr:std::make_shared<const TermBoost>(vocabulary_,kept,TermBoost::default_margin,TermBoost::default_budget));
  return kept.size();
}
void Recognizer::open() {
  if(active_)throw std::runtime_error("previous hearing has not retired");
  fresh_capture_=true;cancelled_.store(false);
}
void Recognizer::begin() {
  if(active_ || epoch_==std::numeric_limits<uint64_t>::max())throw std::runtime_error("recognizer lifecycle");
  if(cancelled_.load())throw aii::voice::Cancelled("multitalker cancelled");
  microphone_.reset(++epoch_,!fresh_capture_);fresh_capture_=false;
  evidence_=SpeakerEvidence(nemotron_?8:4,nemotron_?160:1280);evidence_audio_={};samples_=0;finished_=false;text_={};active_=true;
  if(cancelled_.load()){microphone_.cancel();throw aii::voice::Cancelled("multitalker cancelled");}
}
void Recognizer::append(const std::vector<MicrophoneUpdate>& updates) {
  if(cancelled_.load())throw aii::voice::Cancelled("multitalker cancelled");
  for(const auto& update:updates) {
    if(!update.activity.empty()) {
      evidence_.push(update.activity_start_frame,update.activity);
      evidence_audio_.collect(evidence_.regions());
    }
    for(const auto& track:update.tracks)for(const auto& token:track.tokens) {
    auto& text=text_.at(track.track);const auto& piece=vocabulary_.at(static_cast<size_t>(token.id));
    if(piece.size()>131071-text.size())throw std::runtime_error("speaker transcript extent exceeded");
    text+=piece;
    }
  }
}
std::string Recognizer::push(const float* pcm,size_t count) {
  if(!active_ || finished_)throw std::runtime_error("recognizer is not accepting speech");
  if(count>std::numeric_limits<uint64_t>::max()-samples_)throw std::overflow_error("recognizer sample clock");
  try {
    // Bound lookahead custody even when a caller supplies a large audio batch.
    while(count) {
      const auto n=std::min(count,EvidenceAudio::maximum_chunk);
      evidence_audio_.append(pcm,n);append(microphone_.accept(pcm,n));samples_+=n;pcm+=n;count-=n;
    }
  }
  catch(...) {if(cancelled_.load())throw aii::voice::Cancelled("multitalker cancelled");throw;}
  return {}; // Never collapse distinct speakers into one partial string.
}
std::string Recognizer::finish() {
  return finish_with_progress({});
}
std::string Recognizer::finish_with_progress(const std::function<void()>& completed) {
  if(!active_ || finished_)throw std::runtime_error("recognizer is not accepting speech");
  try {
    append(microphone_.finish(completed));
    if(completed)completed();
    if(samples_){evidence_.finish(samples_);evidence_audio_.collect(evidence_.regions());}
    bool needs_refinement=false,overlap=false;
    for(size_t track=0;track<text_.size();++track) {
      const auto& observed=evidence_.activity(track);
      overlap=overlap||observed.overlap;
      needs_refinement=needs_refinement||(!text_[track].empty()&&
          evidence_audio_.refinement_needed(track,evidence_));
    }
    // Do no extra inference for already usable evidence, insufficient active
    // speech or confident overlap. Refinement is not a separator or a new recognizer.
    if(auto refined=microphone_.refine_evidence(needs_refinement&&!overlap,completed)) {
      evidence_=std::move(refined->activity);evidence_audio_=std::move(refined->audio);
    }
    if(cancelled_.load())throw aii::voice::Cancelled("multitalker cancelled");
    finished_=true;
  }
  catch(...) {if(cancelled_.load())throw aii::voice::Cancelled("multitalker cancelled");throw;}
  return {};
}
std::vector<aii::voice::RecognizedSegment> Recognizer::segments() const {
  if(!finished_)throw std::runtime_error("recognizer finals are not ready");
  std::vector<aii::voice::RecognizedSegment> result;
  for(size_t track=0;track<text_.size();++track) {
    const auto first=text_[track].find_first_not_of(' '),last=text_[track].find_last_not_of(' ');
    if(first==std::string::npos)continue;
    // The utterance extent is conservative, not a claimed word alignment.
    result.push_back({"utterance-"+std::to_string(epoch_)+".track-"+std::to_string(track),
                      text_[track].substr(first,last-first+1),0,samples_});
    const auto& selected=evidence_audio_.attribution_track(track,evidence_);
    result.back().evidence_start=selected.span.start;result.back().evidence=selected.pcm;
    result.back().evidence_regions=selected.regions;
    result.back().evidence_unavailable=evidence_audio_.unavailable_reason(track,evidence_);
  }
  return result;
}
void Recognizer::reset(){active_=false;finished_=false;text_={};samples_=0;evidence_=SpeakerEvidence(nemotron_?8:4,nemotron_?160:1280);evidence_audio_={};}
void Recognizer::cancel() noexcept {cancelled_.store(true);microphone_.cancel();}
}
