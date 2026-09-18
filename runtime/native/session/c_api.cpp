#include "c_api_internal.h"
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstring>
#include <memory>

struct aii_voice_models {
  std::unique_ptr<aii::voice::ModelOwner> owner;
  std::atomic<bool> leased{false};
};
struct aii_voice_session {
  aii_voice_models* models;
  std::unique_ptr<aii::voice::Session> session;
};
namespace {
void need(bool ok,const char* why) { if(!ok)throw std::invalid_argument(why); }
template<size_t N> void copy(char (&to)[N],const char* from) noexcept {
  const auto n=std::min(N-1,std::strlen(from)); std::memcpy(to,from,n);to[n]=0;
}
template<class F> aii_voice_result call(aii_voice_error* error,F f) noexcept {
  if(error)error->message[0]=0;
  try { return f(); }
  catch(const std::invalid_argument& e) { if(error)copy(error->message,e.what());return AII_VOICE_INVALID; }
  catch(const std::exception& e) { if(error)copy(error->message,e.what());return AII_VOICE_FAILED; }
  catch(...) { if(error)copy(error->message,"unknown native embedding failure");return AII_VOICE_FAILED; }
}
aii::voice::Session& get(aii_voice_session* s) { need(s!=nullptr,"session required");return *s->session; }
}
namespace aii::voice {
aii_voice_models* wrap_models(std::unique_ptr<ModelOwner> owner) {
  need(bool(owner),"model owner required");
  auto result=std::make_unique<aii_voice_models>();result->owner=std::move(owner);return result.release();
}
}
extern "C" {
aii_voice_result aii_voice_models_execution(aii_voice_models* m,char* out,size_t capacity,size_t* required,aii_voice_error* e) {
  if(out&&capacity)out[0]=0;
  if(required)*required=0;
  return call(e,[&]{need(m&&required&&(out||!capacity),"models, execution buffer and required size needed");
    const auto text=m->owner->execution_info();need(!text.empty()&&text.size()<=16384,"execution readback bound");
    *required=text.size()+1;if(capacity<*required)return AII_VOICE_CAPACITY;
    std::memcpy(out,text.c_str(),*required);return AII_VOICE_OK;});
}
aii_voice_result aii_voice_models_warm(aii_voice_models* m,aii_voice_readiness* out,aii_voice_error* e) {
  return call(e,[&]{need(m && out,"models and readiness output required");
    bool expected=false;if(!m->leased.compare_exchange_strong(expected,true))return AII_VOICE_BUSY;
    try { *out=m->owner->warm();m->leased=false;return AII_VOICE_OK; }
    catch(...) { m->leased=false;throw; }
  });
}
aii_voice_result aii_voice_prepare_capture(aii_voice_models* m,const float* pcm,size_t count,
    aii_voice_capture* out,aii_voice_error* e) {
  return call(e,[&]{
    need(m&&pcm&&out&&count>=31920&&count<=480000,"complete bounded 16 kHz capture and output required");
    for(size_t i=0;i<count;++i)
      need(std::isfinite(pcm[i])&&pcm[i]>=-1&&pcm[i]<=1,"capture PCM must be finite and within [-1,1]");
    bool expected=false;if(!m->leased.compare_exchange_strong(expected,true))return AII_VOICE_BUSY;
    try {
      const auto capture=m->owner->prepare_capture(std::vector<float>(pcm,pcm+count));
      need(capture.samples==count,"capture evidence span differs");
      auto digest=[](const char* p) {
        for(size_t i=0;i<64;++i)if(!((p[i]>='0'&&p[i]<='9')||(p[i]>='a'&&p[i]<='f')))return false;
        return p[64]==0;
      };
      need(digest(capture.embedding_binding)&&digest(capture.pcm_sha256),"capture binding/digest invalid");
      double norm=0;for(double value:capture.embedding){need(std::isfinite(value),"nonfinite capture embedding");norm+=value*value;}
      need(std::isfinite(norm)&&std::abs(norm-1)<=1e-6,"capture embedding must be a unit vector");
      *out=capture;m->leased=false;return AII_VOICE_OK;
    } catch(...) {m->leased=false;throw;}
  });
}
aii_voice_result aii_voice_models_release(aii_voice_models** m,aii_voice_error* e) {
  return call(e,[&]{need(m && *m,"model handle required");if((*m)->leased.load())return AII_VOICE_BUSY;
    delete *m;*m=nullptr;return AII_VOICE_OK;});
}
aii_voice_result aii_voice_open(aii_voice_models* m,const aii_voice_settings* c,aii_voice_session** out,aii_voice_error* e) {
  return aii_voice_open_configured(m,c,nullptr,out,e);
}
aii_voice_result aii_voice_open_configured(aii_voice_models* m,const aii_voice_settings* c,const aii_voice_speech_settings* speech,aii_voice_session** out,aii_voice_error* e) {
  return aii_voice_open_with_capture_limit(m,c,speech,aii::voice::default_capture_limit_minutes,out,e);
}
aii_voice_result aii_voice_open_with_capture_limit(aii_voice_models* m,const aii_voice_settings* c,const aii_voice_speech_settings* speech,uint32_t minutes,aii_voice_session** out,aii_voice_error* e) {
  const aii_voice_open_options options{c,speech,minutes,1};
  return aii_voice_open_session(m,&options,out,e);
}
aii_voice_result aii_voice_open_session(aii_voice_models* m,const aii_voice_open_options* options,aii_voice_session** out,aii_voice_error* e) {
  return call(e,[&]{need(m && out && !*out,"models and empty output handle required");
    need(options && options->input_enabled<=1,"open options with strict input_enabled boolean required");
    bool expected=false;if(!m->leased.compare_exchange_strong(expected,true))return AII_VOICE_BUSY;
    try {
      aii::voice::Settings settings;
      settings.capture_limit_minutes=options->capture_limit_minutes;
      const auto* c=options->control;
      const auto* speech=options->speech;
      if(c) { settings.pause_ms=c->pause_ms;settings.speech_threshold=c->speech_threshold;settings.input_tail_timeout_ms=c->input_tail_timeout_ms; }
      if(speech) {
        auto bounded=[](const char* p,size_t max) { need(p!=nullptr,"speech setting string required");size_t n=0;while(n<=max && p[n])++n;need(n && n<=max,"speech setting string exceeds bound");return std::string(p,n); };
        settings.speech={bounded(speech->voice,64),bounded(speech->tts_language,16),bounded(speech->stt_language,16),speech->temperature,speech->seed};
      }
      auto h=std::make_unique<aii_voice_session>();h->models=m;
      std::optional<aii::voice::Hearing> hearing;
      if(options->input_enabled)
        hearing.emplace(aii::voice::Hearing{m->owner->recognizer(),m->owner->vad(),m->owner->endpoint(),m->owner->speaker()});
      h->session=std::make_unique<aii::voice::Session>(m->owner->synthesizer(),settings,hearing);
      *out=h.release();return AII_VOICE_OK;
    } catch(...) { m->leased=false;throw; }
  });
}
aii_voice_result aii_voice_release(aii_voice_session** s,aii_voice_error* e) {
  return call(e,[&]{need(s && *s,"session handle required");if(!(*s)->session->status().retired)return AII_VOICE_BUSY;
    auto m=(*s)->models;delete *s;*s=nullptr;m->leased=false;return AII_VOICE_OK;});
}
aii_voice_result aii_voice_feed(aii_voice_session* s,uint64_t start,const float* p,size_t n,aii_voice_error* e) {
  return call(e,[&]{return get(s).feed(start,p,n)?AII_VOICE_OK:AII_VOICE_AGAIN;});
}
aii_voice_result aii_voice_finish_input(aii_voice_session* s,uint64_t end,aii_voice_error* e) {
  return call(e,[&]{get(s).finish_input(end);return AII_VOICE_OK;});
}
aii_voice_result aii_voice_synthesize(aii_voice_session* s,uint64_t id,const char* t,size_t n,aii_voice_error* e) {
  return call(e,[&]{need(t && n && n<=32000 && !std::memchr(t,0,n),"bounded UTF-8 text without embedded NUL required");
    get(s).synthesize(id,std::string(t,n));return AII_VOICE_OK;});
}
aii_voice_result aii_voice_stop_playback(aii_voice_session* s,uint64_t id,aii_voice_error* e) {
  return call(e,[&]{get(s).stop_playback(id);return AII_VOICE_OK;});
}
aii_voice_result aii_voice_cancel_synthesis(aii_voice_session* s,uint64_t id,aii_voice_error* e) {
  return call(e,[&]{get(s).cancel_synthesis(id);return AII_VOICE_OK;});
}
aii_voice_result aii_voice_release_generation(aii_voice_session* s,uint64_t id,aii_voice_error* e) {
  return call(e,[&]{get(s).release_generation(id);return AII_VOICE_OK;});
}
aii_voice_result aii_voice_playback(aii_voice_session* s,uint64_t id,uint64_t n,uint8_t terminal,uint8_t stopped,aii_voice_error* e) {
  return call(e,[&]{need(terminal<=1 && stopped<=1,"strict boolean required");get(s).playback(id,n,terminal,stopped);return AII_VOICE_OK;});
}
aii_voice_result aii_voice_close(aii_voice_session* s,uint8_t abort,aii_voice_error* e) {
  return call(e,[&]{need(abort<=1,"strict boolean required");get(s).close(abort);return AII_VOICE_OK;});
}
aii_voice_result aii_voice_wait(aii_voice_session* s,uint32_t ms,aii_voice_error* e) {
  return call(e,[&]{need(ms<=30000,"wait must be bounded to 30000 ms");return get(s).wait_closed(ms)?AII_VOICE_OK:AII_VOICE_AGAIN;});
}
aii_voice_result aii_voice_status(aii_voice_session* s,aii_voice_snapshot* out,aii_voice_error* e) {
  return call(e,[&]{need(out!=nullptr,"snapshot required");const auto v=get(s).status();*out={};
    out->received=v.received;out->controlled=v.controlled;out->recognized=v.recognized;
    out->generation=v.generation;out->sequence=v.sequence;out->cutoff=v.cutoff;
    out->queued_audio_samples=v.queued_audio_samples;out->synthesis_segments=v.synthesis_segments;out->completed_segments=v.completed_segments;
    out->input_finished=v.input_finished;out->synthesizing=v.synthesizing;out->draining=v.draining;
    out->stopping=v.stopping;out->retired=v.retired;out->aborted=v.aborted;out->closing=v.closing;
    out->cutoff_set=v.cutoff_set;out->recognition_active=v.recognition_active;copy(out->error,v.error.c_str());return AII_VOICE_OK;});
}
aii_voice_result aii_voice_enrollment_finals(aii_voice_session* s,uint64_t* output,size_t capacity,size_t* required,aii_voice_error* e) {
  return call(e,[&]{need(s&&required&&(output||!capacity),"final discovery arguments required");*required=0;
    const auto state=get(s).status();need(!state.closing&&!state.aborted&&!state.retired,"final discovery needs an open session");
    if(!state.input_enabled)return AII_VOICE_OK; // never expose a predecessor's hearing evidence
    const auto finals=s->models->owner->enrollment_finals();need(finals.size()<=16,"native final evidence bound exceeded");
    *required=finals.size();if(capacity<*required)return AII_VOICE_CAPACITY;
    if(*required) {
      std::memcpy(output,finals.data(),finals.size()*sizeof(uint64_t));
    }
    return AII_VOICE_OK;
  });
}
aii_voice_result aii_voice_enroll_selected(aii_voice_session* s,const char* current,size_t n,
    const char* id,const char* label,const uint64_t* finals,size_t count,
    char* output,size_t capacity,size_t* required,aii_voice_error* e) {
  return call(e,[&] {
    need(s&&current&&n&&n<=8u<<20&&id&&label&&finals&&count&&count<=8&&required&&
      (output||!capacity),"bounded enrollment preparation arguments required");
    *required=0;
    auto bounded=[](const char* p,size_t max){size_t n=0;while(n<=max&&p[n])++n;
      need(n&&n<=max,"bounded enrollment string required");return std::string(p,n);};
    const auto speaker=bounded(id,128),name=bounded(label,512);
    const auto status=get(s).status();
    need(status.input_enabled,"session has no input direction");
    need(!status.closing&&!status.aborted&&!status.retired,"enrollment needs an open session");
    auto candidate=s->models->owner->enroll_selected(std::string(current,n),speaker,name,
      std::vector<uint64_t>(finals,finals+count));
    need(candidate.size()<8u<<20,"prepared enrollment exceeds bound");
    *required=candidate.size()+1;
    if(capacity<*required)return AII_VOICE_CAPACITY;
    std::memcpy(output,candidate.c_str(),*required);return AII_VOICE_OK;
  });
}
aii_voice_result aii_voice_generation_status(aii_voice_session* s,uint64_t id,aii_voice_generation* out,aii_voice_error* e) {
  return call(e,[&]{need(out!=nullptr,"generation snapshot required");const auto v=get(s).generation(id);*out={};
    out->generated=v.generated;out->delivered=v.delivered;out->rendered=v.rendered;out->queued=v.queued;
    out->fenced=v.fenced;out->cancelled=v.cancelled;out->retired=v.retired;out->end_taken=v.end_taken;
    out->receipt=v.receipt;out->stopped=v.stopped;return AII_VOICE_OK;});
}
aii_voice_result aii_voice_next_event(aii_voice_session* s,aii_voice_event* out,char* text,size_t capacity,size_t* required,aii_voice_error* e) {
  uint64_t ignored=0;return aii_voice_next_event_with_reference(s,out,&ignored,text,capacity,required,e);
}
aii_voice_result aii_voice_next_event_with_reference(aii_voice_session* s,aii_voice_event* out,uint64_t* reference,char* text,size_t capacity,size_t* required,aii_voice_error* e) {
  return call(e,[&]{need(out && required && (text || !capacity),"event/buffer/size required");
    need(reference!=nullptr,"event reference output required");*reference=0;
    aii::voice::Event event;*required=0;
    if(!get(s).event_bounded(event,capacity,*required))return *required?AII_VOICE_CAPACITY:AII_VOICE_AGAIN;
    *reference=event.refers_to;*out={};out->sequence=event.sequence;out->turn=event.turn;out->generation=event.generation;
    out->start=event.start;out->end=event.end;copy(out->kind,event.kind.c_str());
    std::memcpy(text,event.text.c_str(),*required);return AII_VOICE_OK;});
}
aii_voice_result aii_voice_next_audio(aii_voice_session* s,aii_voice_audio* out,float* pcm,size_t capacity,size_t* required,aii_voice_error* e) {
  return call(e,[&]{need(out && required && (pcm || !capacity),"audio/buffer/size required");
    aii::voice::Audio audio;*required=0;
    if(!get(s).audio_bounded(audio,capacity,*required))return *required?AII_VOICE_CAPACITY:AII_VOICE_AGAIN;
    *out={};out->generation=audio.generation;out->start=audio.start;out->end=audio.end;
    if(*required)std::memcpy(pcm,audio.pcm.data(),*required*sizeof(float));
    return AII_VOICE_OK;});
}
}
