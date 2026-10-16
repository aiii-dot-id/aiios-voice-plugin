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
// What the session says of a model call that passed its time: the model, the
// time the session was given for one call, and the limits table's member.
std::string exceeded(const std::string& name,unsigned milliseconds) {
  return name+" model call exceeded progress deadline: "+std::to_string(milliseconds)+
      " ms, the time the limits table gives it (model_call_ms)";
}
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
  check(s.status().error==exceeded(name,150),"wrong model fault");
  check(s.status().stopping,"model fault left session accepting work");
  check(!s.wait_closed(10),"uncooperative model falsely retired");
  if(site==Stall::Tts){check(s.generation(1).fenced,"timeout did not fence output");Audio out;check(!s.audio(out),"fault leaked queued output");}
  b.released=true;
  check(s.wait_closed(2000),"released model failed to retire");
  check(s.status().error==exceeded(name,150),"cleanup replaced first failure");
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
    check(s.status().error==exceeded("recognition",500),
          "completed stage concealed the following stalled inference");
    b.released=true;
  } else {
    until([&]{return a.complete.load()||!s.status().error.empty();});
    check(a.complete&&s.status().error.empty(),"sequential completed models became cumulative timeout");
    s.close(true);
  }
  check(s.wait_closed(2000),"composite model did not retire");
}
// THE WATCHDOG COVERS THE SPEAKER'S INFERENCE ONLY. An identifier whose
// inference is over and whose storage then takes longer than the model's
// deadline is not a stalled model: the session goes on and its observation
// is delivered. One that never says its inference is over is watched for
// the whole call, as before (the registry's round trips ran inside the
// model's deadline).
void speaker_storage_outlasts_the_model_deadline(bool says_inference_is_over) {
  struct Stored:SpeakerIdentifier {
    bool says;std::atomic<bool> entered{false},released{false};
    explicit Stored(bool s):says(s){}
    std::string identify(uint64_t,const std::vector<float>&)override{
      if(says&&model_part_done)model_part_done();
      entered=true;
      // The storage's part: four times the model's 150 ms.
      const auto due=std::chrono::steady_clock::now()+600ms;
      while(std::chrono::steady_clock::now()<due&&!released)std::this_thread::sleep_for(1ms);
      return R"({"outcome":"uncertain"})";
    }
    void cancel()noexcept override{} // deliberately uncooperative, as the stalled owners above
  };
  Barrier b;A a(b);V v(b);E e(b);T t(b);Stored u(says_inference_is_over);
  Session s(a,v,e,t,settings(),&u);uint64_t offset=0;
  struct Free {Stored& u;~Free(){u.released=true;}} free{u};
  feed(s,offset,32000,.25f);s.finish_input(offset);
  until([&]{return u.entered.load();});
  if(says_inference_is_over) {
    bool observed=false;
    until([&]{Event ev;while(s.event(ev))if(ev.kind=="speaker_observation")observed=true;return observed||!s.status().error.empty();});
    check(s.status().error.empty(),"storage after the speaker's inference was held to the model's deadline");
    check(observed,"the observation was lost");
    s.close(false);
    check(s.wait_closed(2000)&&s.status().error.empty(),"a session whose speaker storage was slow did not end cleanly");
  } else {
    until([&]{return !s.status().error.empty();});
    check(s.status().error==exceeded("speaker identification",150),
          "an identifier that never says its inference is over was not watched");
    u.released=true;
    check(s.wait_closed(2000),"released identifier failed to retire");
  }
}
// THE STATUS SAYS WHAT THE WATCHDOG HOLDS. A composition that calls a quiet
// session stalled reads whether a model call is in flight and how many have
// been given back, so that a call inside its own time is left to that time
// and one that ended is counted as work that moved. A wait for the audio's
// consumer is not a model call and is not reported as one.
// A session's owners make their first calls as their threads start, each in
// its own time. The first look at which none is in flight can come before
// one of them has begun, so that is not yet an idle session: it is idle
// when no call is in flight and the count has stood still.
uint64_t settled(Session& s) {
  uint64_t ended=0;
  until([&]{
    const auto before=s.status();
    std::this_thread::sleep_for(250ms);
    const auto now=s.status();
    ended=now.model_calls_ended;
    return !before.model_call_in_flight&&!now.model_call_in_flight&&now.model_calls_ended==before.model_calls_ended;
  });
  return ended;
}
void status_says_a_model_call_is_in_flight() {
  Barrier b;b.selected=Stall::Tts;A a(b);V v(b);E e(b);T t(b);
  auto given=settings();given.model_call_timeout_ms=5000;
  Session s(a,v,e,t,given);Release release{b};
  const auto idle=settled(s);
  std::this_thread::sleep_for(50ms);
  check(!s.status().model_call_in_flight&&s.status().model_calls_ended==idle,"an idle session reported a model call");
  s.synthesize(1,"A slow reply.");
  until([&]{return b.entered.load();});
  const auto during=s.status();
  check(during.model_call_in_flight,"a model call in flight was not reported");
  std::this_thread::sleep_for(50ms);
  check(s.status().model_calls_ended==during.model_calls_ended,"a call still in flight was counted as ended");
  b.released=true;
  until([&]{return s.generation(1).retired;});
  const auto after=s.status();
  check(!after.model_call_in_flight,"a call that returned was still reported in flight");
  check(after.model_calls_ended>during.model_calls_ended,"a call that returned was not counted as ended");
  s.close(true);check(s.wait_closed(1000),"the session did not retire");
  // Audio waiting for its consumer: the queue is full and no call is held.
  Barrier none;A a2(none);V v2(none);E e2(none);T flood(none);flood.flood=true;
  Session queued(a2,v2,e2,flood,given);
  queued.synthesize(1,"Buffered reply.");
  until([&]{return queued.status().queued_audio_samples>=98304;});
  const auto waiting=settled(queued);
  std::this_thread::sleep_for(50ms);
  check(!queued.status().model_call_in_flight&&queued.status().model_calls_ended==waiting,"a wait for the audio's consumer was reported as a model call");
  queued.close(true);check(queued.wait_closed(1000),"abort stranded the queue's waiter");
}
// THE ENDPOINT'S TWO WAITS ARE THE SESSION'S SETTINGS. Its verdict at a turn's
// commit point is waited for the stated time, here two seconds where the
// header keeps one: then an event says it was late, with the number and the
// table's member, and nothing has failed. A query still owned when the input
// ends is waited for the stated time, here 300 ms inside a model call of five
// seconds, as no carrier states them: the session fails with that wait's
// sentence.
void endpoint_waits_are_the_sessions() {
  Barrier b;b.selected=Stall::Endpoint;A a(b);V v(b);E e(b);T t(b);
  auto given=settings();given.model_call_timeout_ms=5000;
  given.endpoint_decision_timeout_ms=2000;given.endpoint_retire_timeout_ms=300;
  Session s(a,v,e,t,given);Release release{b};uint64_t offset=0;
  const auto began=std::chrono::steady_clock::now();
  feed(s,offset,512,.25f);feed(s,offset,12288,0.f);
  std::string said;
  const auto due=began+20s;
  while(said.empty()) {
    check(std::chrono::steady_clock::now()<due,"a late endpoint verdict was never said");
    check(s.status().error.empty(),"a late endpoint verdict failed the session");
    Event ev;while(s.event(ev))if(ev.kind=="pause_late")said=ev.text;
    std::this_thread::sleep_for(1ms);
  }
  check(std::chrono::steady_clock::now()-began>=1900ms,"the endpoint's verdict was not waited for the session's time");
  check(said=="endpoint verdict late, the turn ends by silence alone: 2000 ms, the time the limits table gives it (endpoint_decision_ms)",
        "a late endpoint verdict was not said with its number and its member");
  s.finish_input(offset);
  until([&]{return !s.status().error.empty();});
  check(s.status().error=="semantic endpoint did not retire: 300 ms, the time the limits table gives it (endpoint_retire_ms)",
        "an endpoint query that did not retire was not said with its number and its member");
  b.released=true;
  check(s.wait_closed(2000),"the released endpoint failed to retire");
}
}
int main(){try {
  stalled(Stall::Vad,"VAD");stalled(Stall::Push,"recognition");
  stalled(Stall::Finish,"recognition");stalled(Stall::Endpoint,"endpoint");
  stalled(Stall::Tts,"synthesis");stalled(Stall::Uid,"speaker identification");
  idle_and_backpressure();
  completed_stages_not_cumulative_timeout(false);
  completed_stages_not_cumulative_timeout(true);
  speaker_storage_outlasts_the_model_deadline(true);
  speaker_storage_outlasts_the_model_deadline(false);
  status_says_a_model_call_is_in_flight();
  endpoint_waits_are_the_sessions();
  std::cout<<"All five inference owners: faults, honest retirement, recovery, idle and backpressure PASS\n";
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
