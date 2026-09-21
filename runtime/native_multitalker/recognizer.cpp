#include "recognizer.h"
#include <limits>

namespace aii::multitalker {
Recognizer::Recognizer(const std::string& root,const float* mel,size_t count,std::vector<std::string> vocab)
    :microphone_(root,mel,count),vocabulary_(std::move(vocab)) {
  if(vocabulary_.size()!=1024)throw std::invalid_argument("multitalker vocabulary census");
  for(auto& word:vocabulary_) {
    if(word.size()>1024)throw std::invalid_argument("multitalker vocabulary token bound");
    size_t at=0;
    while((at=word.find("\xe2\x96\x81",at))!=std::string::npos)word.replace(at++,3," ");
  }
}
std::string Recognizer::execution_info() const {
  return R"({"encoder_provider":"CPUExecutionProvider","diarization_provider":"CPUExecutionProvider","speaker_conditioned":true,"hardware_execution_verified":false})";
}
void Recognizer::open() {
  if(active_)throw std::runtime_error("previous hearing has not retired");
  cancelled_.store(false);
}
void Recognizer::begin() {
  if(active_ || epoch_==std::numeric_limits<uint64_t>::max())throw std::runtime_error("recognizer lifecycle");
  if(cancelled_.load())throw aii::voice::Cancelled("multitalker cancelled");
  microphone_.reset(++epoch_);samples_=0;finished_=false;text_={};active_=true;
  if(cancelled_.load()){microphone_.cancel();throw aii::voice::Cancelled("multitalker cancelled");}
}
void Recognizer::append(const std::vector<MicrophoneUpdate>& updates) {
  if(cancelled_.load())throw aii::voice::Cancelled("multitalker cancelled");
  for(const auto& update:updates)for(const auto& track:update.tracks)for(const auto& token:track.tokens) {
    auto& text=text_.at(track.track);const auto& piece=vocabulary_.at(static_cast<size_t>(token.id));
    if(piece.size()>131071-text.size())throw std::runtime_error("speaker transcript extent exceeded");
    text+=piece;
  }
}
std::string Recognizer::push(const float* pcm,size_t count) {
  if(!active_ || finished_)throw std::runtime_error("recognizer is not accepting speech");
  if(count>std::numeric_limits<uint64_t>::max()-samples_)throw std::overflow_error("recognizer sample clock");
  try {append(microphone_.accept(pcm,count));samples_+=count;}
  catch(...) {if(cancelled_.load())throw aii::voice::Cancelled("multitalker cancelled");throw;}
  return {}; // Never collapse distinct speakers into one partial string.
}
std::string Recognizer::finish() {
  if(!active_ || finished_)throw std::runtime_error("recognizer is not accepting speech");
  try {append(microphone_.finish());finished_=true;}
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
  }
  return result;
}
void Recognizer::reset(){active_=false;finished_=false;text_={};samples_=0;}
void Recognizer::cancel() noexcept {cancelled_.store(true);microphone_.cancel();}
}
