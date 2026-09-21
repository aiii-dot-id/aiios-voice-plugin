#include "c_api_internal.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <iostream>
#include <thread>

using namespace aii::voice;
namespace {
void check(bool ok,const char* reason){if(!ok)throw std::runtime_error(reason);}
struct Asr final:Recognizer {
  size_t samples=0,begins=0,total=0;bool malformed=false,continuous=false,selected=false;
  void begin() override {samples=0;++begins;}
  std::string push(const float*,size_t count) override {samples+=count;total+=count;return {};}
  std::string finish() override {return {};}
  bool separated() const override {return true;}
  bool continuous_input() const override {return continuous;}
  std::vector<RecognizedSegment> segments() const override {
    if(selected)return {{"utterance-1.track-0","first speaker's words",0,samples,0,std::vector<float>(32000,.25f)},
                        {"utterance-1.track-1","second speaker's words",0,samples,32000,std::vector<float>(32000,.75f)}};
    return {{"utterance-1.track-0","first speaker's words",0,samples},
            {"utterance-1.track-1","second speaker's words",samples/2,malformed?samples+1:samples}};
  }
  void reset() override {samples=0;}
  void cancel() noexcept override {}
};
struct V:Vad {void reset() override{} float score(const float* p) override{return *p>.1f?1.f:0.f;}};
struct E:Endpoint {double score(uint64_t,const std::vector<float>&)override{return 1;}void cancel()noexcept override{}};
struct T:Synthesizer {
  void start(uint64_t,const std::string&)override{} std::vector<float> next()override{return {};}
  void reset()override{} void cancel(uint64_t)noexcept override{}
};
struct S:SpeakerIdentifier {
  std::atomic<size_t> calls{0};
  std::atomic<size_t> tracks{0};std::atomic<bool> hold{false},cancelled{false};
  std::string identify(uint64_t,const std::vector<float>&)override{++calls;return "{}";}
  std::string identify_track(uint64_t,const std::vector<float>& pcm)override {
    while(hold&&!cancelled)std::this_thread::sleep_for(std::chrono::milliseconds(1));
    if(cancelled)throw Cancelled("cancelled");
    const auto index=tracks++;
    if(!pcm.empty())check(pcm.size()==32000&&std::all_of(pcm.begin(),pcm.end(),[&](float x){return x==(index?.75f:.25f);}),"track PCM blended or misrouted");
    return R"({"outcome":"unavailable","reason":"fixture_track","used_for_permissions":false})";
  }
  void cancel()noexcept override{cancelled=true;}
};
struct Owner:ModelOwner {
  Asr a;V v;E e;T t;S s;
  Recognizer& recognizer()override{return a;}Vad& vad()override{return v;}
  Endpoint& endpoint()override{return e;}Synthesizer& synthesizer()override{return t;}
  SpeakerIdentifier* speaker()override{return &s;}
};
void run(bool malformed) {
  auto owner=std::make_unique<Owner>();auto* observed=owner.get();observed->a.malformed=malformed;
  auto* models=wrap_models(std::move(owner));aii_voice_session* session=nullptr;aii_voice_error error{};
  check(aii_voice_open(models,nullptr,&session,&error)==AII_VOICE_OK,"session open");
  std::vector<float> pcm(4096,.3f);
  check(aii_voice_feed(session,0,pcm.data(),pcm.size(),&error)==AII_VOICE_OK,"input admitted");
  check(aii_voice_finish_input(session,pcm.size(),&error)==AII_VOICE_OK,"input finish");
  aii_voice_snapshot snapshot{};
  const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5);
  do {
    check(aii_voice_status(session,&snapshot,&error)==AII_VOICE_OK,"status");
    if((snapshot.input_finished&&!snapshot.draining) || *snapshot.error)break;
    check(std::chrono::steady_clock::now()<deadline,"completion deadline");
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  } while(true);
  check(malformed==bool(*snapshot.error),"malformed segment did not fault");
  size_t finals=0,observations=0,commits=0;uint64_t references[2]{};
  aii_voice_event event{};uint64_t reference;char text[1024],track[64];size_t required;
  while(aii_voice_next_event_with_track(session,&event,&reference,track,sizeof track,text,sizeof text,&required,&error)==AII_VOICE_OK) {
    if(std::string(event.kind)=="transcript_final") {
      check(finals<2,"duplicated final");references[finals]=event.sequence;
      check(std::string(track)=="utterance-1.track-"+std::to_string(finals),"track lost at C boundary");
      check(event.end==pcm.size() && event.start==(finals?pcm.size()/2:0),"segment clock changed");
      check(std::string(text)==(finals?"second speaker's words":"first speaker's words"),"words blended");++finals;
    }
    if(std::string(event.kind)=="speaker_observation") {
      check(observations<finals && reference==references[observations],"observation joined to another final");
      check(std::string(track)=="utterance-1.track-"+std::to_string(observations),"observation lost track");++observations;
    }
    commits+=std::string(event.kind)=="turn_committed";
  }
  check(finals==(malformed?0u:2u) && observations==finals && commits==(malformed?0u:1u),"final census");
  check(observed->s.calls==0,"pooled microphone reached UID matcher");
  check(aii_voice_close(session,1,&error)==AII_VOICE_OK,"close");
  check(aii_voice_wait(session,3000,&error)==AII_VOICE_OK,"retirement");
  check(aii_voice_release(&session,&error)==AII_VOICE_OK,"session release");
  check(aii_voice_models_release(&models,&error)==AII_VOICE_OK,"models release");
}
void capture_origin(bool speech) {
  Asr a;a.continuous=true;V v;E e;T t;
  Session session(a,v,e,t,Settings{5000,.5f});
  std::vector<float> pcm(512*88,0);
  if(speech)std::fill(pcm.begin()+512*80,pcm.end(),.3f);
  const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5);
  for(size_t offset=0;offset<pcm.size();offset+=512) {
    while(!session.feed(offset,pcm.data()+offset,512)) {
      check(session.status().error.empty(),"capture fault");
      check(std::chrono::steady_clock::now()<deadline,"feed deadline");
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
  }
  session.finish_input(pcm.size());
  while(!session.status().input_finished) {
    check(session.status().error.empty(),"continuous recognition fault");
    check(std::chrono::steady_clock::now()<deadline,"finish deadline");
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  session.close(true);check(session.wait_closed(2000),"capture retirement");
  check(a.begins==1 && a.total==pcm.size(),"VAD discarded or duplicated capture context");
  size_t finals=0;Event event;
  while(session.event(event))if(event.kind=="transcript_final") {
    check(event.end==pcm.size(),"capture end shifted");
    check(event.start==(finals?pcm.size()/2:0),"capture origin shifted");++finals;
  }
  check(finals==(speech?2u:0u),"silence invented a turn or speech lost a track");
}
void capture_across_pause() {
  Asr a;a.continuous=true;V v;E e;T t;
  Session session(a,v,e,t,Settings{320,.5f});
  uint64_t offset=0;size_t commits=0,finals=0;
  const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5);
  auto drain=[&]{
    check(session.status().error.empty(),"pause transition fault");
    check(std::chrono::steady_clock::now()<deadline,"pause transition deadline");
    Event event;
    while(session.event(event)) {
      commits+=event.kind=="turn_committed";
      if(event.kind=="transcript_final") {
        check(event.start<event.end && event.end<=offset,"post-pause final clock");++finals;
      }
    }
  };
  auto feed=[&](size_t blocks,float value){
    std::vector<float> pcm(512,value);
    for(size_t i=0;i<blocks;++i){
      while(!session.feed(offset,pcm.data(),pcm.size())){drain();std::this_thread::sleep_for(std::chrono::milliseconds(1));}
      offset+=pcm.size();drain();std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
  };
  feed(40,0);feed(8,.3f);feed(40,0);
  while(!commits){drain();std::this_thread::sleep_for(std::chrono::milliseconds(1));}
  check(commits==1 && finals==2,"first pause final census");
  feed(8,.3f);session.finish_input(offset);
  while(!session.status().input_finished){drain();std::this_thread::sleep_for(std::chrono::milliseconds(1));}
  drain();session.close(true);check(session.wait_closed(2000),"pause capture retirement");
  check(commits==2 && finals==4,"second pause final census");
  check(a.total==offset && a.begins==2,"capture skipped or repeated at turn transition");
}
void selected_uid_queue(bool abort) {
  Asr a;a.selected=true;V v;E e;T t;S speaker;speaker.hold=true;
  Session session(a,v,e,t,Settings{5000,.5f},&speaker);
  std::vector<float> pcm(512,.5f);const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5);
  for(uint64_t offset=0;offset<64000;offset+=512)while(!session.feed(offset,pcm.data(),512)) {
    check(std::chrono::steady_clock::now()<deadline,"selected feed deadline");std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  session.finish_input(64000);
  while(!session.status().input_finished){check(std::chrono::steady_clock::now()<deadline,"selected finish deadline");std::this_thread::sleep_for(std::chrono::milliseconds(1));}
  check(session.status().draining,"queued speaker evidence called idle");
  session.close(abort);
  if(!abort){check(!session.wait_closed(10),"drain lost pending track evidence");speaker.hold=false;}
  check(session.wait_closed(2000),"track worker retirement");
  check(!speaker.calls && speaker.tracks==(abort?0u:2u),"track evidence lost or pooled");
  size_t observations=0;Event event;
  while(session.event(event))if(event.kind=="speaker_observation"){
    check(event.track=="utterance-1.track-"+std::to_string(observations++),"asynchronous track binding changed");
  }
  check(observations==(abort?0u:2u),"track observation census");
}
}
int main(){try{run(false);run(true);capture_origin(false);capture_origin(true);capture_across_pause();selected_uid_queue(false);selected_uid_queue(true);return 0;}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
