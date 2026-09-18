#include "c_api_internal.h"
#include <atomic>
#include <chrono>
#include <cstring>
#include <iostream>
#include <thread>

using namespace aii::voice;
namespace {
void check(bool value,const char* reason) { if(!value)throw std::runtime_error(reason); }
template<class F> void until(F f) {
  const auto end=std::chrono::steady_clock::now()+std::chrono::seconds(3);
  while(!f()) {
    check(std::chrono::steady_clock::now()<end,"output-only test timed out");
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
}
struct Tts : Synthesizer {
  std::atomic<bool> entered{false},cancelled{false};
  bool hold=false;unsigned chunks=0;
  void start(uint64_t,const std::string& text) override {
    hold=text=="Hold.";chunks=0;cancelled=false;entered=true;
  }
  std::vector<float> next() override {
    if(hold)until([&]{return cancelled.load();});
    if(cancelled)throw Cancelled("cancelled");
    return chunks++ ? std::vector<float>{} : std::vector<float>(960,.25f);
  }
  void reset() override {}
  void cancel(uint64_t) noexcept override {cancelled=true;}
};
struct OutputOwner : ModelOwner {
  Tts t;
  Recognizer& recognizer() override {throw std::runtime_error("output-only accessed recognizer");}
  Vad& vad() override {throw std::runtime_error("output-only accessed VAD");}
  Endpoint& endpoint() override {throw std::runtime_error("output-only accessed endpoint");}
  SpeakerIdentifier* speaker() override {throw std::runtime_error("output-only accessed UID");}
  Synthesizer& synthesizer() override {return t;}
};
}

int main() {
  aii_voice_models* models=nullptr;aii_voice_session* session=nullptr;aii_voice_error error{};
  try {
    auto owner=std::make_unique<OutputOwner>();auto* held=&owner->t;
    models=wrap_models(std::move(owner));
    aii_voice_open_options options{nullptr,nullptr,30,2};
    check(aii_voice_open_session(models,&options,&session,&error)==AII_VOICE_INVALID&&!session,"nonboolean input admitted");
    options.input_enabled=0;
    check(aii_voice_open_session(models,&options,&session,&error)==AII_VOICE_OK,error.message);
    aii_voice_session* other=nullptr;
    check(aii_voice_open_session(models,&options,&other,&error)==AII_VOICE_BUSY&&!other,"lease was not exclusive");
    float pcm=.25f;
    check(aii_voice_feed(session,0,&pcm,1,&error)==AII_VOICE_INVALID,"absent input accepted PCM");
    check(aii_voice_finish_input(session,0,&error)==AII_VOICE_INVALID,"absent input accepted fabricated cutoff");
    size_t finals=99;
    check(aii_voice_enrollment_finals(session,nullptr,0,&finals,&error)==AII_VOICE_OK&&finals==0,"output-only exposed hearing evidence");
    check(aii_voice_synthesize(session,1,"Hold.",5,&error)==AII_VOICE_OK,error.message);
    until([&]{return held->entered.load();});
    check(aii_voice_stop_playback(session,1,&error)==AII_VOICE_OK,error.message);
    check(!held->cancelled,"stop secretly cancelled inference");
    check(aii_voice_cancel_synthesis(session,1,&error)==AII_VOICE_OK,error.message);
    aii_voice_audio audio{};float out[2048];size_t n=0;
    until([&]{auto rc=aii_voice_next_audio(session,&audio,out,2048,&n,&error);check(rc==AII_VOICE_OK||rc==AII_VOICE_AGAIN,error.message);return rc==AII_VOICE_OK&&audio.end;});
    check(aii_voice_playback(session,1,0,1,1,&error)==AII_VOICE_OK,error.message);
    check(aii_voice_synthesize(session,2,"Recovery.",9,&error)==AII_VOICE_OK,error.message);
    uint64_t delivered=0;
    until([&]{auto rc=aii_voice_next_audio(session,&audio,out,2048,&n,&error);check(rc==AII_VOICE_OK||rc==AII_VOICE_AGAIN,error.message);if(rc==AII_VOICE_OK)delivered+=n;return rc==AII_VOICE_OK&&audio.end;});
    check(delivered==960,"recovery audio changed");
    check(aii_voice_close(session,0,&error)==AII_VOICE_OK,error.message);
    check(aii_voice_wait(session,10,&error)==AII_VOICE_AGAIN,"drained without render receipt");
    check(aii_voice_playback(session,2,delivered,1,0,&error)==AII_VOICE_OK,error.message);
    check(aii_voice_wait(session,3000,&error)==AII_VOICE_OK,error.message);
    aii_voice_snapshot snapshot{};
    check(aii_voice_status(session,&snapshot,&error)==AII_VOICE_OK,error.message);
    check(!snapshot.input_finished&&!snapshot.cutoff_set&&!snapshot.received&&!snapshot.recognition_active,"output-only invented input activity");
    aii_voice_event event{};char text[1024];
    for(;;) {
      auto rc=aii_voice_next_event(session,&event,text,sizeof text,&n,&error);
      if(rc==AII_VOICE_AGAIN)break;
      check(rc==AII_VOICE_OK,error.message);
      check(std::strcmp(event.kind,"input_finished")!=0&&std::strncmp(event.kind,"transcript_",11)!=0,"output-only invented hearing event");
    }
    check(aii_voice_release(&session,&error)==AII_VOICE_OK,error.message);
    check(aii_voice_open_session(models,&options,&session,&error)==AII_VOICE_OK,error.message);
    check(aii_voice_close(session,0,&error)==AII_VOICE_OK,error.message);
    check(aii_voice_wait(session,3000,&error)==AII_VOICE_OK,error.message);
    check(aii_voice_release(&session,&error)==AII_VOICE_OK,error.message);
    check(aii_voice_models_release(&models,&error)==AII_VOICE_OK,error.message);
    std::cout<<"Absent hearing never accessed; receipt drain, interruption, recovery and lease reuse pass\n";
  } catch(const std::exception& e) {
    std::cerr<<e.what()<<'\n';
    if(session) {aii_voice_close(session,1,&error);aii_voice_wait(session,3000,&error);aii_voice_release(&session,&error);}
    if(models)aii_voice_models_release(&models,&error);
    return 1;
  }
}
