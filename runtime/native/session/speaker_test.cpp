#include "session.h"
#include <atomic>
#include <chrono>
#include <functional>
#include <iostream>
#include <thread>
using namespace aii::voice;
using namespace std::chrono_literals;
void check(bool b,const char* reason) {if(!b)throw std::runtime_error(reason);}
template<class F> void until(F f) {
  const auto end=std::chrono::steady_clock::now()+5s;
  while(!f()){check(std::chrono::steady_clock::now()<end,"speaker test deadline");std::this_thread::sleep_for(1ms);}
}
struct Asr:Recognizer {
  void begin() override{} std::string push(const float*,size_t) override{return "retained words";}
  std::string finish() override{return "retained words";}void reset() override{}void cancel() noexcept override{}
};
struct V:Vad {void reset() override{}float score(const float* p) override{return p[0]>.1f?.9f:0.f;}};
struct E:Endpoint {double score(uint64_t,const std::vector<float>&)override{return .9;}void cancel()noexcept override{}};
struct T:Synthesizer {
  std::atomic<bool> entered{false},cancelled{false};
  void start(uint64_t,const std::string&)override{entered=true;}
  std::vector<float> next()override{until([&]{return cancelled.load();});throw Cancelled("fenced");}
  void reset()override{}void cancel(uint64_t)noexcept override{cancelled=true;}
};
struct U:SpeakerIdentifier {
  std::atomic<bool> entered{false},release{false},cancelled{false};
  std::vector<float> heard;uint64_t final=0;
  std::string identify(uint64_t id,const std::vector<float>& p)override {
    final=id;heard=p;entered=true;until([&]{return release.load();});
    return "{\"outcome\":\"known\",\"speaker_id\":\"fixture\",\"used_for_permissions\":false}";
  }
  void cancel()noexcept override{cancelled=true;} // deliberately ignore until released
};
void send(Session& s,const std::vector<float>& p) {
  for(size_t i=0;i<p.size();) {
    const auto n=std::min<size_t>(997,p.size()-i);
    until([&]{return s.feed(i,p.data()+i,n);});i+=n;
  }
  s.finish_input(p.size());
}
void association_drain_interruption() {
  Asr a;V v;E e;T t;U u;Session s(a,v,e,t,{},&u);
  std::vector<float> pcm(32137);for(size_t i=0;i<pcm.size();++i)pcm[i]=float(8192+int(i%997))/32768;
  send(s,pcm);until([&]{return u.entered.load()&&s.status().input_finished;});
  check(s.status().draining,"pending speaker evidence reported idle");
  check(u.heard==pcm,"UID cropped, padded or lost the retained opening/tail samples");
  Event ev,final;size_t finals=0;
  while(s.event(ev))if(ev.kind=="transcript_final"){final=ev;++finals;}
  check(finals==1&&final.sequence==u.final&&final.start==0&&final.end==pcm.size(),"UID final association differs");
  s.synthesize(1,"control must not wait for speaker inference");until([&]{return t.entered.load();});
  s.interrupt(1);until([&]{return t.cancelled.load()&&!s.status().synthesizing;});
  Audio audio;until([&]{return s.audio(audio)&&audio.end;});s.playback(1,0,true,true);
  s.close(false);check(!s.wait_closed(20),"drain discarded pending UID");
  u.release=true;check(s.wait_closed(2000),"completed UID failed to release drain");
  size_t observations=0;
  while(s.event(ev))if(ev.kind=="speaker_observation") {
    ++observations;check(ev.refers_to==final.sequence&&ev.sequence>ev.refers_to&&ev.start==final.start&&ev.end==final.end,
                         "observation referred to a different final");
  }
  check(observations==1&&s.status().error.empty(),"missing/duplicate UID observation");
}
void abort_fence() {
  Asr a;V v;E e;T t;U u;Session s(a,v,e,t,{},&u);
  send(s,std::vector<float>(32000,.25f));until([&]{return u.entered.load();});
  s.close(true);check(u.cancelled,"abort did not request UID cancellation");
  check(!s.wait_closed(20),"UID falsely retired while still running");
  u.release=true;check(s.wait_closed(2000),"UID did not retire");
  Event ev;while(s.event(ev))check(ev.kind!="speaker_observation","late UID escaped abort fence");
}
void bounded_busy() {
  Asr a;V v;E e;T t;U u;Session s(a,v,e,t,Settings{320,.5f},&u);
  uint64_t offset=0;
  auto push=[&](const std::vector<float>& pcm) {
    for(size_t i=0;i<pcm.size();) {
      const auto n=std::min<size_t>(997,pcm.size()-i);
      until([&]{return s.feed(offset,pcm.data()+i,n);});i+=n;offset+=n;
    }
  };
  push(std::vector<float>(32000,.25f));push(std::vector<float>(16000,0));
  until([&]{return u.entered.load();});
  push(std::vector<float>(32000,.25f));s.finish_input(offset);
  until([&]{return s.status().input_finished;});
  Event ev;size_t finals=0,busy=0;uint64_t last=0;
  while(s.event(ev)) {
    if(ev.kind=="transcript_final"){++finals;last=ev.sequence;}
    if(ev.kind=="speaker_observation") {
      ++busy;check(ev.refers_to==last&&ev.text.find("speaker_worker_busy")!=std::string::npos,"busy decision wrongly associated");
    }
  }
  check(finals==2&&busy==1,"slow speaker inference blocked/lost a later final");
  s.close(false);u.release=true;check(s.wait_closed(2000),"busy owner failed to retire");
}
void unavailable_bounds(size_t n,const std::string& reason) {
  Asr a;V v;E e;T t;U u;Session s(a,v,e,t,{},&u);
  send(s,std::vector<float>(n,.25f));until([&]{return s.status().input_finished;});
  s.close(false);check(s.wait_closed(1000),"unavailable UID stranded drain");
  check(!u.entered,"invalid context sent to embedding model");
  Event ev;uint64_t final=0;size_t observations=0;
  while(s.event(ev)) {
    if(ev.kind=="transcript_final")final=ev.sequence;
    if(ev.kind=="speaker_observation"){
      ++observations;check(final&&ev.refers_to==final&&ev.text.find(reason)!=std::string::npos,"unavailability not attributed");
    }
  }
  check(observations==1,"unavailable result missing");
}
void unavailable_enrollment() {
  struct Unreadable:U {
    std::string identify(uint64_t,const std::vector<float>&)override {
      throw EnrollmentUnavailable("do not expose this private broker detail");
    }
  } u;
  Asr a;V v;E e;T t;Session s(a,v,e,t,{},&u);
  send(s,std::vector<float>(32000,.25f));until([&]{return s.status().input_finished;});
  s.close(false);check(s.wait_closed(2000),"unavailable enrollment stranded drain");
  Event ev;size_t observations=0;
  while(s.event(ev))if(ev.kind=="speaker_observation") {
    ++observations;
    check(ev.text=="{\"outcome\":\"unavailable\",\"reason\":\"enrollment_unavailable\",\"used_for_permissions\":false}",
          "enrollment failure became acoustic ambiguity, a score, or private error text");
  }
  check(observations==1,"missing enrollment observation");
}
int main(){try {
  association_drain_interruption();abort_fence();bounded_busy();unavailable_enrollment();
  unavailable_bounds(31919,"utterance_too_short");unavailable_bounds(480001,"utterance_exceeds_uid_context");
  std::cout<<"speaker association, exact PCM, independent stop, drain, abort and context bounds PASS\n";
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
