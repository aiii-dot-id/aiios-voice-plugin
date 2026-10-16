#include "prefix_recognizer.h"
#include <chrono>
#include <condition_variable>
#include <functional>
#include <future>
#include <iostream>
#include <limits>
#include <mutex>
#include <thread>

using namespace aii::voice;
using namespace std::chrono_literals;
void check(bool b,const char* why) { if(!b) throw std::runtime_error(why); }
template<class F> void wait_for(F f) {
  const auto end=std::chrono::steady_clock::now()+3s;
  while(!f()) {check(std::chrono::steady_clock::now()<end,"test wait expired"); std::this_thread::sleep_for(1ms);}
}
template<class E,class F> void refuses(F f,const char* why) {
  try {f();} catch(const E&) {return;} throw std::runtime_error(why);
}
struct State {
  std::mutex mutex;
  std::condition_variable changed;
  std::vector<std::vector<float>> calls;
  std::thread::id owner;
  std::atomic<bool> hold{false}, entered{false}, destroyed{false}, same_thread{true}, fail{false};
  std::atomic<bool> hold_second{false}, second_entered{false};
};
struct Decoder:PrefixDecoder {
  std::shared_ptr<State> state;
  explicit Decoder(std::shared_ptr<State> s):state(std::move(s)) {}
  void open() override {state->owner=std::this_thread::get_id();}
  std::string decode(const std::vector<float>& pcm,const std::atomic<bool>&) override {
    state->same_thread=state->same_thread && state->owner==std::this_thread::get_id();
    size_t ordinal;
    {std::lock_guard<std::mutex> lock(state->mutex); state->calls.push_back(pcm);ordinal=state->calls.size();}
    state->entered=true;
    // Deliberately ignore cancellation: late GPU readback must still be fenced
    // by the adapter rather than depending on a cooperative fake backend.
    {std::unique_lock<std::mutex> lock(state->mutex); check(state->changed.wait_for(lock,3s,[&]{return !state->hold;}),"held decoder expired");}
    if(ordinal==2) {
      state->second_entered=true;
      std::unique_lock<std::mutex> lock(state->mutex);
      check(state->changed.wait_for(lock,3s,[&]{return !state->hold_second;}),"held final decoder expired");
    }
    if(state->fail) throw std::runtime_error("accelerator readback failed");
    return "opening words retained through sample "+std::to_string(pcm.size());
  }
  ~Decoder() override {state->same_thread=state->same_thread && state->owner==std::this_thread::get_id(); state->destroyed=true;}
};
void release(const std::shared_ptr<State>& s) {s->hold=false;s->changed.notify_all();}
void coalescing_exact_tail_and_affinity() {
  auto s=std::make_shared<State>();s->hold=true;s->hold_second=true;
  {
    PrefixRecognizer a(std::make_unique<Decoder>(s),{4,4,1024});a.open();a.begin();
    std::vector<float> pcm(321);for(size_t i=0;i<pcm.size();++i)pcm[i]=float(i);
    a.push(pcm.data(),4);wait_for([&]{return s->entered.load();});
    const auto start=std::chrono::steady_clock::now();
    for(size_t i=4;i<pcm.size();++i)a.push(pcm.data()+i,1);
    check(std::chrono::steady_clock::now()-start<100ms,"input waited behind held inference");
    auto done=std::async(std::launch::async,[&]{return a.finish();});
    check(done.wait_for(20ms)==std::future_status::timeout,"final invented before exact inference");
    release(s);wait_for([&]{return s->second_entered.load();});
    const auto final_held=done.wait_for(20ms)==std::future_status::timeout;
    s->hold_second=false;s->changed.notify_all();
    check(final_held,"stale partial substituted for final while final inference held");
    check(done.get()=="opening words retained through sample 321","stale partial substituted for final");
    {
      std::lock_guard<std::mutex> lock(s->mutex);
      check(s->calls.size()==2,"partial requests were queued, not coalesced");
      check(s->calls.back()==pcm,"opening samples or non-hop-aligned tail lost");
    }
    check(a.finish()=="opening words retained through sample 321","repeat final changed");
    refuses<std::invalid_argument>([&]{a.push(pcm.data(),1);},"post-finish input admitted");
    a.reset();a.begin();a.push(pcm.data(),7);check(a.finish().find("sample 7")!=std::string::npos,"fresh turn failed");a.reset();
  }
  check(s->destroyed && s->same_thread,"accelerator lifecycle moved across threads");
}
void cancellation_and_retirement() {
  auto s=std::make_shared<State>();s->hold=true;
  PrefixRecognizer a(std::make_unique<Decoder>(s),{4,4,128});a.open();a.begin();float pcm[9]{};
  a.push(pcm,9);wait_for([&]{return s->entered.load();});
  auto finish=std::async(std::launch::async,[&]{refuses<Cancelled>([&]{a.finish();},"cancelled final escaped");});
  const auto start=std::chrono::steady_clock::now();a.cancel();
  check(std::chrono::steady_clock::now()-start<50ms,"cancel waited for GPU inference");
  check(finish.wait_for(100ms)==std::future_status::ready,"cancelled finish did not unblock");finish.get();
  auto reset=std::async(std::launch::async,[&]{a.reset();});
  check(reset.wait_for(20ms)==std::future_status::timeout,"retirement declared while GPU still held");
  release(s);reset.get();
  refuses<Cancelled>([&]{a.begin();},"cancelled session reopened without open");
  a.open();a.begin();a.push(pcm,5);check(a.finish().find("sample 5")!=std::string::npos,"late old result contaminated recovery");a.reset();
}
void bounds_and_backend_failure() {
  auto s=std::make_shared<State>();PrefixRecognizer a(std::make_unique<Decoder>(s),{4,4,8});
  a.open();a.begin();float pcm[9]{};
  refuses<std::invalid_argument>([&]{a.push(pcm,9);},"oversize audio truncated silently");
  float bad=std::numeric_limits<float>::quiet_NaN();
  refuses<std::invalid_argument>([&]{a.push(&bad,1);},"NaN accepted");
  refuses<std::invalid_argument>([&]{a.push(nullptr,1);},"null input accepted");
  a.push(pcm,7);check(a.finish().find("sample 7")!=std::string::npos,"refused input mutated retained PCM");a.reset();
  a.begin();a.push(pcm,3);refuses<std::invalid_argument>([&]{a.finish();},"short utterance silently padded");a.reset();
  a.begin();check(a.finish().empty(),"empty input invented words");a.reset();
  a.begin();s->fail=true;a.push(pcm,8);
  refuses<std::runtime_error>([&]{a.finish();},"failed inference fell back to previous final");
  refuses<std::runtime_error>([&]{a.reset();},"retirement hid inference failure");
  refuses<std::runtime_error>([&]{a.open();},"failed accelerator silently reopened");
}
void cancellation_does_not_hide_failure() {
  auto s=std::make_shared<State>();s->hold=true;s->fail=true;
  PrefixRecognizer a(std::make_unique<Decoder>(s),{4,4,8});a.open();a.begin();float pcm[8]{};
  a.push(pcm,8);wait_for([&]{return s->entered.load();});a.cancel();release(s);
  try {a.reset();throw std::logic_error("failure laundered into cancellation");}
  catch(const std::runtime_error& e) {check(std::string(e.what())=="accelerator readback failed","retirement lost actual fault");}
}
struct Detector:Vad {
  void reset() override {}
  float score(const float* pcm) override {for(size_t i=0;i<512;++i)if(pcm[i]>.1f)return .9f;return 0;}
};
struct EndpointModel:Endpoint {
  std::atomic<size_t> queries{0};
  double score(uint64_t,const std::vector<float>&) override {++queries;return .9;}
  void cancel() noexcept override {}
};
struct Tts:Synthesizer {
  std::atomic<bool> hold{false},cancelled{false},entered{false};
  int chunks=0;
  void start(uint64_t,const std::string&) override {chunks=0;cancelled=false;entered=true;}
  std::vector<float> next() override {
    if(chunks && hold) wait_for([&]{return cancelled.load() || !hold;});
    if(cancelled)throw Cancelled("TTS cancelled");
    if(chunks++==2)return {};return std::vector<float>(960,.2f);
  }
  void cancel(uint64_t) noexcept override {cancelled=true;}
  void reset() override {}
};
void real_session_pause_interruption_and_recovery() {
  auto state=std::make_shared<State>();state->hold=true;
  PrefixRecognizer asr(std::make_unique<Decoder>(state),{320,1024,320000});
  Detector vad;EndpointModel end;Tts tts;tts.hold=true;
  Session session(asr,vad,end,tts,Settings{512,.5f});
  session.synthesize(1,"An active reply that must stop on speech.");
  Audio audio;wait_for([&]{return session.audio(audio);});
  check(!audio.end && audio.pcm.size()==960,"test did not deliver active synthesis");
  uint64_t admitted=0;
  auto send=[&](const std::vector<float>& pcm){
    for(size_t i=0;i<pcm.size();) {
      const size_t n=std::min(size_t(241),pcm.size()-i);
      wait_for([&]{return session.feed(admitted,pcm.data()+i,n);});i+=n;admitted+=n;
    }
  };
  send(std::vector<float>(2048,.5f));wait_for([&]{return state->entered.load();});
  wait_for([&]{return tts.cancelled.load() && !session.status().synthesizing;});
  session.playback(1,960,true,true);
  while(session.audio(audio))check(audio.end && audio.pcm.empty(),"stale TTS escaped barge-in fence");
  send(std::vector<float>(16000,0));
  wait_for([&]{return end.queries.load()>0;});
  check(state->hold,"test released inference before pause query");
  Event event;bool pause=false;while(session.event(event)) {
    pause|=event.kind=="pause_query";
    check(event.kind!="transcript_final","unfinished inference became final");
  }
  check(pause,"semantic pause processing waited behind decoder inference");
  release(state);
  size_t finals=0;std::vector<uint64_t> final_sizes;
  auto events=[&]{while(session.event(event))if(event.kind=="transcript_final"){
    ++finals;check(event.start<event.end,"final lost audio extent");
    const auto exact=event.end-event.start;final_sizes.push_back(exact);
    const auto count=std::stoull(event.text.substr(std::string("opening words retained through sample ").size()));
    // A semantic boundary deliberately retains provisional silence for the
    // next turn. The transcript span includes that detector context, not a
    // promise that every trailing-silence sample reached ASR. Test speech and
    // exact decoder input, rather than reinterpreting that existing contract.
    std::lock_guard<std::mutex> lock(state->mutex);
    const auto& decoded=state->calls.back();
    check(count==decoded.size() && count<=exact,"final is not the exact completed decoder snapshot");
    size_t speech=0;for(float x:decoded) speech+=x>.1f;
    check(speech==(finals==1?2048u:4097u),"opening words or speech tail lost in session composition");
  }};
  wait_for([&]{events();return finals==1;});
  send(std::vector<float>(4097,.6f));
  session.finish_input(admitted);
  wait_for([&]{events();return session.status().input_finished;});
  check(finals==2 && session.status().recognized==admitted,"continuous second utterance or final tail lost");
  tts.hold=false;session.synthesize(2,"Complete recovery reply.");
  wait_for([&]{return !session.status().synthesizing;});
  size_t recovered=0;bool ended=false;
  while(session.audio(audio)){check(audio.generation==2,"old generation revived");recovered+=audio.pcm.size();ended|=audio.end;}
  check(ended && recovered==1920,"recovery reply incomplete");
  session.close(false);check(!session.wait_closed(20),"drain guessed playback");
  session.playback(2,recovered,true,false);check(session.wait_closed(1000),"receipt did not release full session");
  check(session.status().error.empty(),"full prefix composition faulted");
}
int main() {
  try {
    coalescing_exact_tail_and_affinity();cancellation_and_retirement();bounds_and_backend_failure();
    cancellation_does_not_hide_failure();real_session_pause_interruption_and_recovery();
    std::cout<<"PASS exact-prefix coalescing, final tail, nonblocking cancel, truthful retirement, recovery, thread affinity, bounds, inference failure\n";
  } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
