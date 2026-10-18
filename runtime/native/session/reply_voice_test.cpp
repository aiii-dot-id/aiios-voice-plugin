#include "session.h"
#include "text.h"
#include <atomic>
#include <chrono>
#include <iostream>
#include <mutex>
#include <thread>
using namespace aii::voice;
using namespace std::chrono_literals;
using Clock=std::chrono::steady_clock;
static void check(bool b,const char* reason) {if(!b)throw std::runtime_error(reason);}
template<class F> static void until(F f) {
  const auto end=Clock::now()+5s;
  while(!f()){check(Clock::now()<end,"reply voice test deadline");std::this_thread::sleep_for(1ms);}
}
struct Voices:Synthesizer {
  mutable std::mutex m;
  std::string voice;
  std::vector<std::string> configured;
  std::vector<std::string> spoke;
  std::vector<Clock::time_point> started;
  std::atomic<bool> hold{false},cancelled{false};
  unsigned n=0;
  void check(const SpeechSettings& s) const override {
    if(s.voice=="nobody")throw std::invalid_argument("voice nobody is not installed for English");
  }
  void configure(const SpeechSettings& s) override {
    if(s.voice=="nobody"||s.voice=="removed")throw std::invalid_argument("voice "+s.voice+" is not installed for English");
    std::lock_guard<std::mutex> l(m);voice=s.voice;configured.push_back(s.voice);
  }
  void start(uint64_t,const std::string&) override {
    std::lock_guard<std::mutex> l(m);spoke.push_back(voice);started.push_back(Clock::now());n=0;cancelled=false;
  }
  std::vector<float> next() override {
    while(hold&&!cancelled)std::this_thread::sleep_for(1ms);
    if(cancelled)throw Cancelled("fenced");
    return n++?std::vector<float>{}:std::vector<float>(480,.25f);
  }
  void reset() override {}
  void cancel(uint64_t) noexcept override {cancelled=true;}
  std::vector<std::string> spoken() const {std::lock_guard<std::mutex> l(m);return spoke;}
  size_t segments() const {std::lock_guard<std::mutex> l(m);return spoke.size();}
  Clock::time_point last_start() const {std::lock_guard<std::mutex> l(m);return started.back();}
};
static SpeechSettings voice(const char* name) {SpeechSettings s;s.voice=name;return s;}
static void spoke(const Voices& t,const std::vector<std::string>& want,const char* reason) {
  const auto got=t.spoken();
  if(got==want)return;
  std::string text=reason;text+=": spoke";
  for(const auto& v:got)text+=" "+v;
  throw std::runtime_error(text);
}
static void say(Session& s,Voices& t,uint64_t generation,const char* text="One. Two.") {
  const auto before=t.segments();
  s.synthesize(generation,text);
  until([&]{return !s.status().synthesizing&&t.segments()>before;});
  Audio a;uint64_t rendered=0;bool end=false;
  until([&]{while(s.audio(a)){rendered+=a.pcm.size();end|=a.end;}return end;});
  s.playback(generation,rendered,true,false);
}
template<class F> static bool refused_at_once(F f,const char* word) {
  try{f();}catch(const std::invalid_argument& e){return std::string(e.what()).find(word)!=std::string::npos;}
  return false;
}
static void the_voice_asked_for_is_the_next_replys() {
  Voices t;Session s(t);
  check(s.speech_state().speech.voice=="alba"&&s.speech_state().revision==0,"a session did not open in the voice it was given");
  say(s,t,1);
  s.speech(voice("javert"));
  check(s.speech_state().speech.voice=="alba"&&s.speech_state().revision==0&&t.configured.size()==1,
        "a voice asked for was taken before a reply began");
  say(s,t,2);say(s,t,3);
  spoke(t,{"alba","javert","javert"},"the voice asked for was not the next reply's and the one after");
  auto state=s.speech_state();
  check(state.speech.voice=="javert"&&state.revision==1&&state.refused.empty(),"the voice in force was not said");
  check(t.configured.size()==2,"the voice was configured again for a reply that asked for no change");
  s.speech(voice("javert"));say(s,t,4);
  check(s.speech_state().revision==1&&t.configured.size()==2,"asking for the voice in force was taken as a change");
  s.close(false);check(s.wait_closed(2000),"the session did not end");
}
static void never_a_part_of_the_reply_being_spoken() {
  Voices t;Session s(t);
  std::string text;
  for(const char* part:{"first","second","third"})
    text+=std::string("This is the ")+part+" part of a reply that is long enough to be spoken in more than one segment, so that a change of voice asked for while it is being spoken has somewhere to land if it were allowed to. ";
  const auto parts=split_text(text).size();
  check(parts>=3,"the reply was not split into several segments");
  t.hold=true;
  s.synthesize(1,text);
  until([&]{return t.segments()==1;});
  s.speech(voice("javert"));
  t.hold=false;
  until([&]{return !s.status().synthesizing;});
  spoke(t,std::vector<std::string>(parts,"alba"),"a reply changed voice between its segments");
  Audio a;uint64_t rendered=0;bool end=false;
  until([&]{while(s.audio(a)){rendered+=a.pcm.size();end|=a.end;}return end;});
  s.playback(1,rendered,true,false);
  say(s,t,2,"Four.");
  check(t.spoken().back()=="javert","the voice asked for during a reply was not the next reply's");
  s.close(false);check(s.wait_closed(2000),"the session did not end");
}
static void a_reply_waits_for_settings_still_being_asked_for() {
  Voices t;Session s(t);
  auto asked=Clock::now();
  s.speech_pending(200);say(s,t,1,"One.");
  auto waited=t.last_start()-asked;
  check(waited>=180ms&&waited<1500ms&&t.spoken().back()=="alba","a reply did not wait its bound for settings that never came");
  std::thread answer([&]{std::this_thread::sleep_for(30ms);s.speech(voice("javert"));});
  asked=Clock::now();
  s.speech_pending(2000);say(s,t,2,"Two.");
  waited=t.last_start()-asked;answer.join();
  check(waited<1000ms&&t.spoken().back()=="javert","a reply waited on after its settings had been answered, or missed them");
  std::thread same([&]{std::this_thread::sleep_for(30ms);s.speech_unchanged();});
  asked=Clock::now();
  s.speech_pending(2000);say(s,t,3,"Three.");
  waited=t.last_start()-asked;same.join();
  check(waited<1000ms&&t.spoken().back()=="javert","a reply waited on after being told nothing changed");
  const auto before=t.segments();
  s.speech_pending(2000);s.synthesize(4,"Four.");
  asked=Clock::now();
  s.cancel_synthesis(4);
  until([&]{return !s.status().synthesizing;});
  check(Clock::now()-asked<1000ms&&t.segments()==before,"a cancelled reply stayed in its wait, or spoke");
  check(refused_at_once([&]{s.speech_pending(0);},"1..2000")&&refused_at_once([&]{s.speech_pending(2001);},"1..2000"),"a wait outside its range was taken");
  s.close(true);check(s.wait_closed(2000),"the session did not end");
}
static void what_cannot_be_taken_never_costs_a_reply() {
  Voices t;Session s(t);
  check(refused_at_once([&]{s.speech(voice("nobody"));},"not installed"),"a voice the backend does not hold was not refused at once");
  auto french=voice("alba");french.tts_language="fr";
  check(refused_at_once([&]{s.speech(french);},"new session"),"a change of language was taken within a session");
  auto hot=voice("alba");hot.temperature=1.5f;
  check(refused_at_once([&]{s.speech(hot);},"temperature"),"a variation out of range was taken");
  check(refused_at_once([&]{s.speech(voice(""));},"preset name"),"an empty voice was taken");
  say(s,t,1,"One.");
  check(t.spoken().back()=="alba"&&s.speech_state().revision==0,"a refused change touched the voice in force");
  s.speech(voice("removed"));
  say(s,t,2,"Two.");
  auto state=s.speech_state();
  check(t.spoken().back()=="alba"&&state.speech.voice=="alba","a change refused where it was applied cost the reply its voice");
  check(state.revision==1&&state.refused.find("removed is not installed")!=std::string::npos,"a change refused where it was applied was not said");
  check(s.status().error.empty(),"a change refused where it was applied failed the session");
  s.speech(voice("javert"));say(s,t,3,"Three.");
  state=s.speech_state();
  check(t.spoken().back()=="javert"&&state.revision==2&&state.refused.empty(),"a change after a refused one was not taken");
  auto varied=voice("javert");varied.temperature=.7f;varied.seed=7;
  s.speech(varied);say(s,t,4,"Four.");
  state=s.speech_state();
  check(state.revision==3&&state.speech.temperature==.7f&&state.speech.seed==7,"the variation and the seed were not taken with the voice");
  s.close(false);check(s.wait_closed(2000),"the session did not end");
}
int main(){try {
  the_voice_asked_for_is_the_next_replys();
  never_a_part_of_the_reply_being_spoken();
  a_reply_waits_for_settings_still_being_asked_for();
  what_cannot_be_taken_never_costs_a_reply();
  std::cout<<"the voice is the next reply's: taken between replies, waited for within a bound, refused without costing a reply PASS\n";
} catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
