// Model double for the pinned diarization C API. This executable supplies
// nemo_speech_diar_* itself and never links NeMo-Speech.cpp or a model. Like
// the native AOSC cache, its speaker memory survives next_utterance and is
// moved by every pushed sample, so any replay fed to the live stream changes
// the next utterance.
#include "nemotron.h"
#include "refinement.h"
#include <nemo_speech/diar.h>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>

struct nemo_speech_diar_model {int unused=0;};
struct nemo_speech_diar_stream {
  double memory=0;            // speaker memory: kept across utterances
  std::vector<float> pending; // samples short of one 10 ms frame
  std::vector<float> probs;   // retained frames, eight channels each
  int64_t base=0;
  bool finished=false;
  size_t pushes=0;
  bool operator==(const nemo_speech_diar_stream& o) const {
    return memory==o.memory&&pending==o.pending&&probs==o.probs&&base==o.base&&
           finished==o.finished&&pushes==o.pushes;
  }
};
namespace {
thread_local std::string last_error;
int open_streams=0;
size_t copies=0,pushes_until_fault=SIZE_MAX;
double contest=0;  // memory sensitivity of a competing channel's mask
nemo_speech_diar_stream* live=nullptr;  // most recently opened, not copied
nemo_speech_asr_status refuse(const char* why) {
  last_error=why;return NEMO_SPEECH_ASR_ERROR_INVALID_ARGUMENT;
}
void emit(nemo_speech_diar_stream& s,const float* x,size_t n) {
  double mean=0;for(size_t i=0;i<n;++i)mean+=x[i];mean/=double(n);
  const float m=float(s.memory/1000);  // never saturates in these fixtures
  float p[8];
  for(auto& v:p)v=.02f+.01f*m;
  p[0]=mean>0?.8f+.1f*m:.1f+.05f*m;
  p[2]=std::min(1.f,float(.02+contest*s.memory));
  s.probs.insert(s.probs.end(),p,p+8);
  s.memory+=std::abs(mean);
}
}
extern "C" {
const char* nemo_speech_asr_last_error(void) {return last_error.c_str();}
nemo_speech_asr_status nemo_speech_diar_create(const nemo_speech_diar_model_config* c,nemo_speech_diar_model** out) {
  if(!c||!out||c->size<sizeof(*c))return refuse("config");
  *out=new nemo_speech_diar_model;return NEMO_SPEECH_ASR_OK;
}
void nemo_speech_diar_destroy(nemo_speech_diar_model* m) {delete m;}
int32_t nemo_speech_diar_num_speakers(const nemo_speech_diar_model*) {return 8;}
double nemo_speech_diar_seconds_per_frame(const nemo_speech_diar_model*) {return .01;}
nemo_speech_asr_status nemo_speech_diar_stream_open(nemo_speech_diar_model* m,nemo_speech_diar_stream** out) {
  if(!m||!out)return refuse("open");
  *out=live=new nemo_speech_diar_stream;++open_streams;return NEMO_SPEECH_ASR_OK;
}
nemo_speech_asr_status nemo_speech_diar_stream_clone(const nemo_speech_diar_stream* s,nemo_speech_diar_stream** out) {
  if(!s||!out)return refuse("clone");
  *out=new nemo_speech_diar_stream(*s);++open_streams;++copies;return NEMO_SPEECH_ASR_OK;
}
void nemo_speech_diar_stream_close(nemo_speech_diar_stream* s) {
  if(!s)return;
  if(s==live)live=nullptr;
  --open_streams;delete s;
}
nemo_speech_asr_status nemo_speech_diar_stream_push_f32(nemo_speech_diar_stream* s,const float* x,size_t n,int32_t rate) {
  if(!s||!x||rate!=16000)return refuse("push");
  if(s->finished)return NEMO_SPEECH_ASR_OK;  // the pinned post-finish contract
  if(!pushes_until_fault--){last_error="injected inference fault";return NEMO_SPEECH_ASR_ERROR_RUNTIME;}
  ++s->pushes;s->pending.insert(s->pending.end(),x,x+n);
  size_t used=0;
  for(;s->pending.size()-used>=160;used+=160)emit(*s,s->pending.data()+used,160);
  s->pending.erase(s->pending.begin(),s->pending.begin()+long(used));
  return NEMO_SPEECH_ASR_OK;
}
nemo_speech_asr_status nemo_speech_diar_stream_finish(nemo_speech_diar_stream* s) {
  if(!s)return refuse("finish");
  if(!s->finished&&!s->pending.empty())emit(*s,s->pending.data(),s->pending.size());
  s->pending.clear();s->finished=true;return NEMO_SPEECH_ASR_OK;
}
nemo_speech_asr_status nemo_speech_diar_stream_next_utterance(nemo_speech_diar_stream* s) {
  if(!s||!s->finished)return refuse("next utterance requires a finished stream");
  s->probs.clear();s->base=0;s->finished=false;return NEMO_SPEECH_ASR_OK;
}
int64_t nemo_speech_diar_frame_count(const nemo_speech_diar_stream* s) {
  return s?s->base+int64_t(s->probs.size()/8):0;
}
int64_t nemo_speech_diar_frame_probs_start(const nemo_speech_diar_stream* s) {return s?s->base:0;}
nemo_speech_asr_status nemo_speech_diar_stream_discard_before(nemo_speech_diar_stream* s,int64_t frame) {
  if(!s||frame<s->base||frame>nemo_speech_diar_frame_count(s))return refuse("discard");
  s->probs.erase(s->probs.begin(),s->probs.begin()+long((frame-s->base)*8));s->base=frame;
  return NEMO_SPEECH_ASR_OK;
}
nemo_speech_asr_status nemo_speech_diar_frame_probs_range(const nemo_speech_diar_stream* s,int64_t first,size_t frames,float* out,size_t capacity) {
  if(!s||!out||first<s->base||first+int64_t(frames)>nemo_speech_diar_frame_count(s)||frames*8>capacity)
    return refuse("range");
  std::memcpy(out,s->probs.data()+(first-s->base)*8,frames*8*sizeof(float));return NEMO_SPEECH_ASR_OK;
}
}

namespace {
using namespace aii::multitalker;
void check(bool ok,const std::string& why){if(!ok)throw std::runtime_error(why);}
std::vector<float> tone(size_t samples,float value){return std::vector<float>(samples,value);}
// One utterance as Microphone consumes it: 997-sample pushes, every frame read
// once and discarded, masks recorded per 14-mask ASR chunk.
std::vector<float> utterance(Nemotron& d,const std::vector<float>& pcm,RefinementCapture* capture) {
  std::vector<float> fine;uint64_t read=0;
  auto drain=[&]{
    const auto end=d.frames();
    if(end>read){auto part=d.range(read,end-read);fine.insert(fine.end(),part.begin(),part.end());}
    read=end;d.discard_before(end);
  };
  for(size_t at=0;at<pcm.size();at+=997) {
    const auto n=std::min(size_t(997),pcm.size()-at);
    if(capture)capture->append(pcm.data()+at,n);
    d.push(pcm.data()+at,n);drain();
  }
  d.finish();drain();
  if(capture) {
    for(size_t at=0;at<fine.size();at+=14*64) {
      const auto end=std::min(fine.size(),at+14*64);
      capture->targets(nemotron_targets({fine.begin()+long(at),fine.begin()+long(end)},(end-at+63)/64));
    }
    capture->seal(fine.size()/8);
  }
  return fine;
}
size_t differing(const std::vector<float>& a,const std::vector<float>& b) {
  size_t n=a.size()>b.size()?a.size()-b.size():b.size()-a.size();
  for(size_t i=0;i<std::min(a.size(),b.size());++i)n+=a[i]!=b[i];
  return n;
}
const NemotronConfig config{"double.gguf",-1};
const auto first=tone(48000,.25f),second=tone(32000,-.5f);
// The continuation after no replay, and what a replay fed to the live stream
// (the replaced behaviour) computed: both on fresh sessions of the double.
struct Reference {std::vector<float> next,replay;};
Reference reference() {
  Reference r;
  {Nemotron d(config);d.reset(false);utterance(d,first,nullptr);d.reset(true);r.next=utterance(d,second,nullptr);}
  Nemotron d(config);d.reset(false);utterance(d,first,nullptr);d.reset(true);
  for(size_t at=0;at<first.size();at+=16000)d.push(first.data()+at,std::min(size_t(16000),first.size()-at));
  d.finish();r.replay=d.range(0,size_t(d.frames()));
  return r;
}
void accepted_or_refused(bool expect_accepted) {
  contest=expect_accepted?0:.02;
  const auto r=reference();
  Nemotron d(config);d.reset(false);RefinementCapture capture;
  utterance(d,first,&capture);check(capture.available(),"capture unavailable");
  const auto before=*live;size_t completed=0;
  const auto fine=d.replay(capture.pcm(),capture.frames(),[]{return false;},[&]{++completed;});
  check(fine==r.replay,"replay did not start from the live pass's memory");
  check(capture.matches(fine)==expect_accepted,expect_accepted?"fixture must preserve masks":"fixture must change masks");
  check(completed==(first.size()+15999)/16000+1,"completed model calls");
  check(open_streams==1,"replay copy was not closed");
  const bool moved=!(*live==before);
  d.reset(true);const auto n=differing(utterance(d,second,nullptr),r.next);
  check(!moved&&!n,std::string(expect_accepted?"accepted":"refused")+" replay "+
        (moved?"moved the live stream; ":"")+std::to_string(n)+" of "+
        std::to_string(r.next.size())+" next-utterance values differ");
}
// Cancel and fault retire the copy and leave the live stream untouched.
void interrupted(bool fault) {
  contest=0;
  const auto r=reference();
  Nemotron d(config);d.reset(false);RefinementCapture capture;utterance(d,first,&capture);
  const auto before=*live;size_t calls=0;bool failed=false;
  if(fault)pushes_until_fault=1;
  try{d.replay(capture.pcm(),capture.frames(),[&]{return !fault&&calls==2;},[&]{++calls;});}
  catch(const std::runtime_error& e){
    failed=std::string(e.what()).find(fault?"injected inference fault":"refinement cancelled")!=std::string::npos;
  }
  pushes_until_fault=SIZE_MAX;
  check(failed,fault?"fault was not reported":"cancel was not reported");
  check(open_streams==1,"interrupted replay copy was not closed");
  const bool moved=!(*live==before);
  d.reset(true);const auto n=differing(utterance(d,second,nullptr),r.next);
  check(!moved&&!n,std::string(fault?"faulted":"cancelled")+" replay "+(moved?"moved the live stream; ":"")+
        std::to_string(n)+" of "+std::to_string(r.next.size())+" next-utterance values differ");
}
}
int main(){try{
  size_t failed=0;
  for(const auto& run:{std::function<void()>([]{accepted_or_refused(true);}),
                       std::function<void()>([]{accepted_or_refused(false);}),
                       std::function<void()>([]{interrupted(false);}),
                       std::function<void()>([]{interrupted(true);})}) {
    // Report every scenario; a failed one may leave its stream to the next.
    try{run();}catch(const std::exception& e){++failed;std::cerr<<e.what()<<'\n';}
    contest=0;pushes_until_fault=SIZE_MAX;
  }
  check(!failed,std::to_string(failed)+" of 4 replay scenarios failed");
  {
    // A replay whose frame extent differs reports nothing and still closes.
    contest=0;Nemotron d(config);d.reset(false);RefinementCapture capture;utterance(d,first,&capture);
    check(d.replay(capture.pcm(),capture.frames()+1,[]{return false;},{}).empty(),"extent mismatch admitted");
    check(open_streams==1,"mismatched replay copy was not closed");
    // The copy refuses what the live stream refuses: no replay before finish.
    d.reset(true);d.push(first.data(),160);
    bool refused=false;try{d.replay(capture.pcm(),capture.frames(),[]{return false;},{});}catch(const std::exception&){refused=true;}
    check(refused&&open_streams==1,"replay before finish admitted");
    // Pushing to a copy changes only the copy.
    const auto before=*live;auto c=d.copy();c->push(first.data(),16000);
    check(*live==before&&c->frames()==d.frames()+100,"copy is not independent");
  }
  check(open_streams==0&&copies==7,"stream lifecycle accounting");
  std::cout<<"Nemotron refinement replay isolation passed\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
