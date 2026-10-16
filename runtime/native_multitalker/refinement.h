#pragma once
#include "nemotron_targets.h"
#include "evidence_audio.h"

namespace aii::multitalker {
struct RefinedEvidence {
  SpeakerEvidence activity{8,160};
  EvidenceAudio audio;
};
// Exact ASR reuse, not a similarity heuristic. Retain at most 32 seconds of
// this utterance and the binary masks actually passed to ASR, in chunk order.
// No transcript, label, enrollment or previous utterance enters this guard.
class RefinementCapture {
 public:
  static constexpr size_t maximum_samples=32*16000;
  void append(const float* pcm,size_t count) {
    if(sealed_)throw std::runtime_error("refinement capture is sealed");
    if(!available_)return;
    if(!pcm || !count || count>16000)throw std::invalid_argument("refinement PCM extent");
    for(size_t i=0;i<count;++i)if(!std::isfinite(pcm[i]) || std::abs(pcm[i])>1)
      throw std::invalid_argument("refinement PCM range");
    if(count>maximum_samples-pcm_.size()){clear();return;}
    pcm_.insert(pcm_.end(),pcm,pcm+count);
  }
  void targets(const std::vector<float>& targets) {
    if(sealed_)throw std::runtime_error("refinement capture is sealed");
    if(!available_)return;
    if(targets.empty() || targets.size()%8 || targets.size()>128*8)
      throw std::invalid_argument("refinement target extent");
    if(targets.size()/8>maximum_samples/1280+128-masks_.size()){clear();return;}
    chunks_.push_back(targets.size()/8);
    for(size_t f=0;f<targets.size();f+=8) {
      uint8_t mask=0;
      for(size_t t=0;t<8;++t) {
        const auto p=targets[f+t];
        if(!std::isfinite(p) || p<0 || p>1)throw std::invalid_argument("refinement target probability");
        if(p>.5f)mask|=uint8_t(1u<<t);
      }
      masks_.push_back(mask);
    }
  }
  void seal(uint64_t frames) {
    if(sealed_)throw std::runtime_error("refinement already sealed");
    sealed_=true;frames_=frames;
    if(!frames || frames>(pcm_.size()+159)/160+1 || masks_.empty())clear();
  }
  bool available() const {return available_ && sealed_ && !pcm_.empty();}
  const std::vector<float>& pcm() const {return pcm_;}
  uint64_t frames() const {return frames_;}
  bool matches(const std::vector<float>& fine) const {
    if(!available() || fine.size()!=frames_*8)return false;
    for(float p:fine)if(!std::isfinite(p) || p<0 || p>1)return false;
    size_t offset=0;
    for(auto frames:chunks_) {
      const auto first=offset*64,end=std::min(fine.size(),first+frames*64);
      const auto targets=nemotron_targets(first<end?
          std::vector<float>(fine.begin()+first,fine.begin()+end):std::vector<float>{},frames);
      for(size_t f=0;f<frames;++f) {
        uint8_t mask=0;
        for(size_t t=0;t<8;++t)if(targets[f*8+t]>.5f)mask|=uint8_t(1u<<t);
        if(mask!=masks_.at(offset+f))return false;
      }
      offset+=frames;
    }
    return offset==masks_.size();
  }
  RefinedEvidence evidence(const std::vector<float>& fine) const {
    if(!matches(fine))throw std::invalid_argument("refinement changed ASR conditioning");
    RefinedEvidence result;
    // Replay evidence collection only, keeping the same bounded ring and
    // selection policy. A padded frame never supplies nonexistent PCM.
    size_t frame=0;
    for(size_t offset=0;offset<pcm_.size();offset+=16000) {
      const auto n=std::min(size_t(16000),pcm_.size()-offset);
      result.audio.append(pcm_.data()+offset,n);
      const auto end=std::min<size_t>(frames_,(offset+n)/160);
      if(end>frame) {
        result.activity.push(frame,{fine.begin()+frame*8,fine.begin()+end*8});
        result.audio.collect(result.activity.regions());frame=end;
      }
    }
    if(frame<frames_) {
      result.activity.push(frame,{fine.begin()+frame*8,fine.end()});
      result.audio.collect(result.activity.regions());
    }
    result.activity.finish(pcm_.size());result.audio.collect(result.activity.regions());
    return result;
  }
  void clear() {
    available_=false;
    std::vector<float>().swap(pcm_);std::vector<size_t>().swap(chunks_);
    std::vector<uint8_t>().swap(masks_);
  }
 private:
  bool available_=true,sealed_=false;
  uint64_t frames_=0;
  std::vector<float> pcm_;
  std::vector<size_t> chunks_;
  std::vector<uint8_t> masks_;
};
}
