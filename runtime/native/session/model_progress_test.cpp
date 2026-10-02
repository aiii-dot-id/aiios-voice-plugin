#include "session.h"
#include <atomic>
#include <chrono>
#include <iostream>
#include <thread>

using namespace aii::voice;
using namespace std::chrono_literals;
namespace {
void check(bool ok,const char* message){if(!ok)throw std::runtime_error(message);}
template<class F> void until(F f) {
  const auto due=std::chrono::steady_clock::now()+3s;
  while(!f()){check(std::chrono::steady_clock::now()<due,"progress test timed out");std::this_thread::sleep_for(1ms);}
}
enum class Stall {None,Vad,Push,Finish,Endpoint,Tts,Uid};
struct Barrier {
  Stall selected=Stall::None;
  std::atomic<bool> entered{false},released{false};
  void call(Stall site) {
    if(site!=selected)return;
    entered=true;
    while(!released)std::this_thread::sleep_for(1ms);
  }
};
struct Release {Barrier& b;~Release(){b.released=true;}};
struct V:Vad {
  Barrier& b;explicit V(Barrier& v):b(v){}
  void reset()override{}
  float score(const float* p)override{b.call(Stall::Vad);return p[0]>.1f?.9f:0.f;}
};
struct A:Recognizer {
  Barrier& b;explicit A(Barrier& v):b(v){}
  void begin()override{}
  std::string push(const float*,size_t)override{b.call(Stall::Push);return "fixture words";}
  std::string finish()override{b.call(Stall::Finish);return "fixture words";}
  void reset()override{}
  void cancel()noexcept override{} // deliberately uncooperative inference
};
struct E:Endpoint {
  Barrier& b;explicit E(Barrier& v):b(v){}
  double score(uint64_t,const std::vector<float>&)override{b.call(Stall::Endpoint);return .9;}
  void cancel()noexcept override{}
};
struct T:Synthesizer {
  Barrier& b;unsigned chunks=0;bool flood=false;
  explicit T(Barrier& v):b(v){}
  void start(uint64_t,const std::string&)override{chunks=0;}
  std::vector<float> next()override{
    b.call(Stall::Tts);
    return chunks++<(flood?10u:1u)?std::vector<float>(32768,.25f):std::vector<float>{};
  }
  void reset()override{}
  void cancel(uint64_t)noexcept override{}
};
struct U:SpeakerIdentifier {
  Barrier& b;explicit U(Barrier& v):b(v){}
  std::string identify(uint64_t,const std::vector<float>&)override{
    b.call(Stall::Uid);return R"({"outcome":"uncertain"})";
  }
  void cancel()noexcept override{}
};
Settings settings(){Settings s;s.model_call_timeout_ms=150;return s;}
void feed(Session& s,uint64_t& offset,size_t count,float value) {
  const std::vector<float> pcm(count,value);
  for(size_t i=0;i<count;) {
    const auto n=std::min<size_t>(512,count-i);
    until([&]{return s.feed(offset,pcm.data()+i,n);});offset+=n;i+=n;
  }
}
void stalled(Stall site,const char* name) {
  Barrier b;b.selected=site;A a(b);V v(b);E e(b);T t(b);U u(b);
  Session s(a,v,e,t,settings(),&u);Release release{b};
  uint64_t offset=0;
  if(site==Stall::Tts)s.synthesize(1,"A stalled reply.");
  else if(site==Stall::Endpoint){feed(s,offset,512,.25f);feed(s,offset,10240,0.f);}
  else {
    feed(s,offset,site==Stall::Uid?32000:512,.25f);
    if(site==Stall::Finish||site==Stall::Uid)s.finish_input(offset);
  }
  until([&]{return b.entered.load();});
  // The control path stays responsive, but must not disguise stuck inference.
  until([&]{return !s.status().error.empty();});
  check(s.status().error==std::string(name)+" model call exceeded progress deadline","wrong model fault");
  check(s.status().stopping,"model fault left session accepting work");
  check(!s.wait_closed(10),"uncooperative model falsely retired");
  if(site==Stall::Tts){check(s.generation(1).fenced,"timeout did not fence output");Audio out;check(!s.audio(out),"fault leaked queued output");}
  b.released=true;
  check(s.wait_closed(2000),"released model failed to retire");
  check(s.status().error==std::string(name)+" model call exceeded progress deadline","cleanup replaced first failure");
  // Only after verified retirement can these model owners be reused.
  b.selected=Stall::None;
  Session recovery(t,settings());recovery.synthesize(1,"Recovery.");
  Audio out;uint64_t samples=0;
  until([&]{if(!recovery.audio(out))return false;samples+=out.pcm.size();return out.end;});
  check(samples==32768,"recovery lost audio");
  recovery.playback(1,samples,true,false);recovery.close(false);
  check(recovery.wait_closed(1000)&&recovery.status().error.empty(),"recovery failed");
}
void idle_and_backpressure() {
  Barrier b;A a(b);V v(b);E e(b);T t(b);
  Session s(a,v,e,t,settings());uint64_t offset=0;
  std::this_thread::sleep_for(200ms);
  check(s.status().error.empty(),"idle silence timed out");
  for(int i=0;i<20;++i){feed(s,offset,512,0.f);std::this_thread::sleep_for(10ms);}
  check(s.status().error.empty(),"healthy calls became cumulative timeout");
  t.flood=true;s.synthesize(1,"Buffered reply.");
  until([&]{return s.status().queued_audio_samples>=98304;});
  std::this_thread::sleep_for(250ms);
  check(s.status().error.empty(),"audio-consumer backpressure became model timeout");
  s.close(true);check(s.wait_closed(1000),"abort stranded queue waiter");
}
void completed_stages_not_cumulative_timeout(bool stall) {
  struct Composite:A {
    bool stall;std::atomic<bool> complete{false};
    Composite(Barrier& b,bool s):A(b),stall(s){}
    std::string finish_with_progress(const std::function<void()>& completed) override {
      for(int i=0;i<10;++i) {
        std::this_thread::sleep_for(100ms);
        completed(); // One completed synchronous inference, not a heartbeat.
        if(stall&&i==0)b.call(Stall::Finish);
      }
      complete=true;return "fixture words";
    }
  };
  Barrier b;b.selected=stall?Stall::Finish:Stall::None;
  Composite a(b,stall);V v(b);E e(b);T t(b);
  // Each simulated call takes 100 ms, while their complete sequence takes
  // at least one second. A 500 ms test lease preserves the cumulative-timeout
  // falsifier without giving a hosted runner only 50 ms of scheduling slack.
  // The stalled-owner tests above retain their 150 ms lease, and no production
  // timeout is changed. Removing completed() must still fail this test.
  auto composite_settings=settings();composite_settings.model_call_timeout_ms=500;
  Session s(a,v,e,t,composite_settings);Release release{b};uint64_t offset=0;
  feed(s,offset,512,.25f);s.finish_input(offset);
  if(stall) {
    until([&]{return b.entered.load();});
    until([&]{return !s.status().error.empty();});
    check(s.status().error=="recognition model call exceeded progress deadline",
          "completed stage concealed the following stalled inference");
    b.released=true;
  } else {
    until([&]{return a.complete.load()||!s.status().error.empty();});
    check(a.complete&&s.status().error.empty(),"sequential completed models became cumulative timeout");
    s.close(true);
  }
  check(s.wait_closed(2000),"composite model did not retire");
}
}
int main(){try {
  stalled(Stall::Vad,"VAD");stalled(Stall::Push,"recognition");
  stalled(Stall::Finish,"recognition");stalled(Stall::Endpoint,"endpoint");
  stalled(Stall::Tts,"synthesis");stalled(Stall::Uid,"speaker identification");
  idle_and_backpressure();
  completed_stages_not_cumulative_timeout(false);
  completed_stages_not_cumulative_timeout(true);
  std::cout<<"All five inference owners: faults, honest retirement, recovery, idle and backpressure PASS\n";
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
