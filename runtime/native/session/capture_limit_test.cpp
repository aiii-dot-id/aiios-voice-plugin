#include "c_api_internal.h"
#include <atomic>
#include <chrono>
#include <functional>
#include <iostream>
#include <thread>

using namespace aii::voice;
using namespace std::chrono_literals;
namespace {
void check(bool ok,const char* message) {if(!ok)throw std::runtime_error(message);}
template<class F> void until(F f) {
  const auto deadline=std::chrono::steady_clock::now()+10s;
  while(!f()) {
    check(std::chrono::steady_clock::now()<deadline,"capture test timeout");
    std::this_thread::yield();
  }
}
struct Asr:Recognizer {
  std::atomic<uint64_t> samples{0};
  void begin() override {}
  std::string push(const float*,size_t n) override {samples+=n;return "opening words retained";}
  std::string finish() override {return "opening words retained";}
  void reset() override {}
  void cancel() noexcept override {}
};
struct Detector:Vad {
  void reset() override {}
  float score(const float* p) override {return p[0]>.1f ? .9f : 0;}
};
struct End:Endpoint {
  double score(uint64_t,const std::vector<float>&) override {return .9;}
  void cancel() noexcept override {}
};
struct Tts:Synthesizer {
  unsigned chunks=0;
  void start(uint64_t,const std::string&) override {chunks=0;}
  std::vector<float> next() override {return chunks++ ? std::vector<float>{} : std::vector<float>(960,.2f);}
  void reset() override {}
  void cancel(uint64_t) noexcept override {}
};
struct Models:ModelOwner {
  Asr a;Detector v;End e;Tts t;
  Recognizer& recognizer() override {return a;}
  Vad& vad() override {return v;}
  Endpoint& endpoint() override {return e;}
  Synthesizer& synthesizer() override {return t;}
  aii_voice_readiness warm() override {return {4,1,"fixture"};}
};
void send(Session& s,uint64_t end,float value=0) {
  std::vector<float> pcm(4096,value);
  while(s.status().received<end) {
    const auto start=s.status().received;
    const auto count=std::min<uint64_t>(pcm.size(),end-start);
    until([&]{return s.feed(start,pcm.data(),count);});
  }
}
void finite_tail() {
  Asr a;Detector v;End e;Tts t;Settings settings;settings.capture_limit_minutes=1;
  Session s(a,v,e,t,settings);
  const auto end=capture_samples(1);
  send(s,end-4096);send(s,end,.5f); // no explicit Finish: exact limit finalizes speech
  until([&]{return s.status().input_finished;});
  const auto snap=s.status();
  check(snap.received==end && snap.recognized==end && snap.cutoff==end && snap.cutoff_set,"finite cutoff lost accepted tail");
  check(!snap.retired && snap.error.empty(),"finite input cutoff killed response opportunity");
  Event event;size_t finals=0,completed=0;
  while(s.event(event)) {
    if(event.kind=="transcript_final") {++finals;check(event.end==end && event.text=="opening words retained","last words lost");}
    if(event.kind=="input_finished") {++completed;check(event.text=="capture_limit" && event.start==end && event.end==end,"finite completion reason/clocks missing");}
  }
  check(finals==1 && completed==1,"finite completion duplicated or missing");
  s.finish_input(end); // identical explicit Finish is idempotent
  bool refused=false;float sample=0;
  try{s.feed(end,&sample,1);}catch(const std::invalid_argument&){refused=true;}
  check(refused && s.status().received==end,"audio beyond finite cutoff admitted");
  s.synthesize(1,"Final response.");until([&]{return !s.status().synthesizing;});
  Audio audio;uint64_t count=0;while(s.audio(audio))count+=audio.pcm.size();
  s.playback(1,count,true,false);s.close(false);
  check(s.wait_closed(1000) && !s.status().aborted,"finite completion failed to drain");
}
void unlimited_and_abort() {
  Asr a;Detector v;End e;Tts t;Settings settings;settings.capture_limit_minutes=0;
  Session s(a,v,e,t,settings);
  const auto end=capture_samples(30)+1025;
  send(s,end-1025);send(s,end,.5f);
  until([&]{return s.status().controlled>=end-512;});
  check(!s.status().cutoff_set && !s.status().input_finished && !s.status().stopping,"zero retained an automatic duration stop");
  s.finish_input(end);until([&]{return s.status().input_finished;});
  check(s.status().recognized==end && s.status().received==end,"unlimited Finish lost partial final block");
  Event event;bool final=false;
  while(s.event(event))if(event.kind=="transcript_final")final=event.end==end;
  check(final,"unlimited opening/tail words missing");
  // Stop/cancel and a recovery reply still work after the former 30-minute cap.
  s.synthesize(1,"Stopped reply.");until([&]{return !s.status().synthesizing;});
  s.stop_playback(1);s.cancel_synthesis(1);
  Audio audio;while(s.audio(audio))check(audio.pcm.empty(),"stale audio after stop");
  s.playback(1,0,true,true);
  s.synthesize(2,"Recovery.");until([&]{return !s.status().synthesizing;});
  uint64_t samples=0;while(s.audio(audio))samples+=audio.pcm.size();
  check(samples==960,"recovery absent after old boundary");
  s.playback(2,samples,true,false);s.close(false);
  check(s.wait_closed(1000),"unlimited Finish did not drain");
  Asr b;Detector w;End f;Tts u;Session abortable(b,w,f,u,settings);
  send(abortable,end);abortable.close(true);
  check(abortable.wait_closed(1000)&&abortable.status().aborted,"zero disabled Abort");
}
void embedding_and_positive_extension() {
  auto m=wrap_models(std::make_unique<Models>());aii_voice_error error{};aii_voice_session* s=nullptr;
  check(aii_voice_open_with_capture_limit(m,nullptr,nullptr,0,&s,&error)==AII_VOICE_OK,"embedding cannot open unlimited");
  // Admission beyond the previous limit must pass through the actual ABI.
  check(aii_voice_finish_input(s,capture_samples(30)+1,&error)==AII_VOICE_OK,"embedding retains old Finish cap");
  check(aii_voice_close(s,1,&error)==AII_VOICE_OK && aii_voice_wait(s,1000,&error)==AII_VOICE_OK,"embedding Abort failed");
  check(aii_voice_release(&s,&error)==AII_VOICE_OK,"embedding release failed");
  check(aii_voice_open_configured(m,nullptr,nullptr,&s,&error)==AII_VOICE_OK,"old ABI failed");
  check(aii_voice_finish_input(s,capture_samples(30)+1,&error)==AII_VOICE_INVALID,"old ABI default changed");
  aii_voice_close(s,1,&error);aii_voice_wait(s,1000,&error);aii_voice_release(&s,&error);
  check(aii_voice_models_release(&m,&error)==AII_VOICE_OK,"model lease leaked");
  Asr a;Detector v;End e;Tts t;Settings settings;settings.capture_limit_minutes=31;
  Session extended(a,v,e,t,settings);
  send(extended,capture_samples(30)+1);
  check(!extended.status().cutoff_set,"positive duration remained 30 minutes");
  extended.finish_input(capture_samples(30)+1);until([&]{return extended.status().input_finished;});
  extended.close(false);check(extended.wait_closed(1000),"extended session did not drain");
  check(capture_samples(UINT32_MAX)<=input_clock_max,"minute conversion exceeds exact wire clock");
}
}
int main() {
  try {finite_tail();unlimited_and_abort();embedding_and_positive_extension();
    std::cout<<"finite tail, unlimited >30min, positive extension, ABI defaults, Finish/Stop/Cancel/recovery/Abort passed\n";}
  catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
