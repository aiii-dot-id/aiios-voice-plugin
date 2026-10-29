#include "nemotron.h"
#include <nemo_speech/diar.h>
#include <algorithm>
#include <cmath>
#include <mutex>
#include <stdexcept>

namespace aii::multitalker {
namespace {
void check(nemo_speech_asr_status s) {
  if(s!=NEMO_SPEECH_ASR_OK)throw std::runtime_error(std::string("Nemotron: ")+nemo_speech_asr_last_error());
}
}
struct NemotronModel::Impl {
  nemo_speech_diar_model* model=nullptr;
  std::mutex lane;
  ~Impl(){nemo_speech_diar_destroy(model);}
};
NemotronModel::NemotronModel(const NemotronConfig& c):p_(std::make_unique<Impl>()) {
  nemo_speech_diar_model_config config{};
  config.size=sizeof(config);config.model_path=c.model.c_str();config.gpu=c.gpu;
  config.preset="v3-streaming";config.left_context_frames=-1;
  check(nemo_speech_diar_create(&config,&p_->model));
  if(nemo_speech_diar_num_speakers(p_->model)!=8 ||
     std::abs(nemo_speech_diar_seconds_per_frame(p_->model)-.01)>1e-8)
    throw std::runtime_error("Nemotron requires eight channels at ten milliseconds");
}
NemotronModel::~NemotronModel()=default;
struct Nemotron::Impl {
  std::shared_ptr<NemotronModel> model;
  nemo_speech_diar_stream* stream=nullptr;
  bool ended=false,faulted=false;
  std::mutex& lane() const {return model->impl().lane;}
  ~Impl(){if(stream){std::lock_guard<std::mutex> l(lane());nemo_speech_diar_stream_close(stream);}}
};
Nemotron::Nemotron(std::unique_ptr<Impl> impl):p_(std::move(impl)) {}
Nemotron::Nemotron(const NemotronConfig& c):p_(std::make_unique<Impl>()) {
  p_->model=c.loaded?c.loaded:std::make_shared<NemotronModel>(c);
}
Nemotron::~Nemotron()=default;
void Nemotron::reset(bool continuation) {
  if(continuation) {
    if(!p_->stream || !p_->ended || p_->faulted)throw std::runtime_error("Nemotron continuation requires retired utterance");
    std::lock_guard<std::mutex> l(p_->lane());
    check(nemo_speech_diar_stream_next_utterance(p_->stream));
  } else {
    std::lock_guard<std::mutex> l(p_->lane());
    nemo_speech_diar_stream_close(p_->stream);p_->stream=nullptr;
    check(nemo_speech_diar_stream_open(p_->model->impl().model,&p_->stream));
  }
  p_->ended=p_->faulted=false;
}
void Nemotron::push(const float* samples,size_t count) {
  if(!p_->stream || p_->ended || p_->faulted || !samples || !count || count>16000)
    throw std::invalid_argument("Nemotron input lifecycle or extent");
  for(size_t i=0;i<count;++i)if(!std::isfinite(samples[i]) || samples[i]<-1 || samples[i]>1)
    throw std::invalid_argument("Nemotron sample range");
  try {std::lock_guard<std::mutex> l(p_->lane());check(nemo_speech_diar_stream_push_f32(p_->stream,samples,count,16000));}
  catch(...) {p_->faulted=true;throw;}
}
void Nemotron::finish() {
  if(!p_->stream || p_->ended || p_->faulted)throw std::runtime_error("Nemotron is not open");
  try {std::lock_guard<std::mutex> l(p_->lane());check(nemo_speech_diar_stream_finish(p_->stream));p_->ended=true;}
  catch(...) {p_->faulted=true;throw;}
}
uint64_t Nemotron::frames() const {
  std::lock_guard<std::mutex> l(p_->lane());
  const auto n=nemo_speech_diar_frame_count(p_->stream);
  if(n<0)throw std::runtime_error("Nemotron negative frame clock");
  return static_cast<uint64_t>(n);
}
uint64_t Nemotron::retained_frames() const {
  const auto all=frames();
  std::lock_guard<std::mutex> l(p_->lane());
  return all-static_cast<uint64_t>(nemo_speech_diar_frame_probs_start(p_->stream));
}
void Nemotron::discard_before(uint64_t frame) {
  if(frame>INT64_MAX)throw std::invalid_argument("Nemotron discard clock");
  std::lock_guard<std::mutex> l(p_->lane());
  check(nemo_speech_diar_stream_discard_before(p_->stream,static_cast<int64_t>(frame)));
}
std::unique_ptr<Nemotron> Nemotron::copy() const {
  if(!p_->stream)throw std::runtime_error("Nemotron copy requires an open stream");
  auto impl=std::make_unique<Impl>();
  impl->model=p_->model;
  {std::lock_guard<std::mutex> l(p_->lane());check(nemo_speech_diar_stream_clone(p_->stream,&impl->stream));}
  impl->ended=p_->ended;impl->faulted=p_->faulted;
  return std::unique_ptr<Nemotron>(new Nemotron(std::move(impl)));
}
std::vector<float> Nemotron::replay(const std::vector<float>& pcm,uint64_t frames,
                                    const std::function<bool()>& cancelled,
                                    const std::function<void()>& completed) const {
  const auto replica=copy();
  replica->reset(true);
  for(size_t offset=0;offset<pcm.size();offset+=16000) {
    if(cancelled())throw std::runtime_error("refinement cancelled");
    replica->push(pcm.data()+offset,std::min(size_t(16000),pcm.size()-offset));
    if(completed)completed();
  }
  if(cancelled())throw std::runtime_error("refinement cancelled");
  replica->finish();
  if(completed)completed();
  if(cancelled())throw std::runtime_error("refinement cancelled");
  std::vector<float> fine;
  if(replica->frames()==frames) {
    for(uint64_t frame=0;frame<frames;frame+=1024) {
      auto part=replica->range(frame,std::min<uint64_t>(1024,frames-frame));
      fine.insert(fine.end(),part.begin(),part.end());
    }
  }
  return fine;
}
std::vector<float> Nemotron::range(uint64_t first,size_t count) const {
  if(first>INT64_MAX || !count || count>1024 || p_->faulted)
    throw std::invalid_argument("Nemotron probability range");
  std::vector<float> p(count*8);
  {std::lock_guard<std::mutex> l(p_->lane());
   check(nemo_speech_diar_frame_probs_range(p_->stream,static_cast<int64_t>(first),count,p.data(),p.size()));}
  for(float x:p)if(!std::isfinite(x) || x<0 || x>1)throw std::runtime_error("Nemotron probability invalid");
  return p;
}
}
