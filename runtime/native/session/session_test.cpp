#include "session.h"
#include "text.h"
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <functional>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <thread>

using namespace aii::voice;
using namespace std::chrono_literals;
void check(bool value,const char* message) { if(!value) throw std::runtime_error(message); }
template<class F> void refuses(F f,const char* message) {
  try { f(); } catch(const std::invalid_argument&) { return; }
  throw std::runtime_error(message);
}
template<class F> void until(F f) {
  auto end=std::chrono::steady_clock::now()+5s;
  while(!f()) {
    if(std::chrono::steady_clock::now()>end) throw std::runtime_error("test observation timeout");
    std::this_thread::sleep_for(1ms);
  }
}
struct Asr:Recognizer {
  std::atomic<bool> entered{false},released{true},cancelled{false};
  std::atomic<uint64_t> samples{0};
  void begin() override {}
  std::string push(const float*,size_t n) override {
    entered=true;
    until([&]{return released.load() || cancelled.load();});
    if(cancelled) throw Cancelled("cancelled ASR");
    samples+=n; return "opening words retained";
  }
  std::string finish() override { return samples?"opening words retained":""; }
  void reset() override {}
  void cancel() noexcept override { cancelled=true; }
};
struct Detector:Vad {
  void reset() override {}
  float score(const float* p) override { for(size_t i=0;i<512;++i) if(p[i]>.1f) return .9f; return 0; }
};
struct End:Endpoint {
  double score(uint64_t,const std::vector<float>&) override { return .9; }
  void cancel() noexcept override {}
};
struct Tts:Synthesizer {
  std::atomic<uint64_t> through{0},generation{0};
  std::atomic<bool> entered{false},hold{false},fault_on_cancel{false},blocked{false},release{false};
  std::atomic<size_t> starts{0};
  size_t hold_segment=0;
  std::vector<std::string> texts;
  int chunks=0;
  void start(uint64_t id,const std::string& text) override {
    generation=id;chunks=0;blocked=false;texts.push_back(text);++starts;entered=true;
  }
  std::vector<float> next() override {
    if(chunks==1 && (hold || (hold_segment && starts==hold_segment))) { blocked=true; until([&]{return through>=generation || release.load();}); }
    if(through>=generation) {
      if(fault_on_cancel) throw std::runtime_error("inference failure, not cancellation");
      throw Cancelled("TTS fenced");
    }
    if(chunks++==2) return {};
    return std::vector<float>(960,.2f);
  }
  void reset() override {}
  void cancel(uint64_t id) noexcept override {
    auto old=through.load(); while(old<id && !through.compare_exchange_weak(old,id)) {}
  }
};
void send(Session& s,const std::vector<float>& pcm,size_t packet) {
  for(size_t i=0;i<pcm.size();) {
    const size_t n=std::min(packet,pcm.size()-i);
    until([&]{return s.feed(i,pcm.data()+i,n);}); i+=n;
  }
}
void admission_and_receipts() {
  Asr a; Detector v; End e; Tts t; Session s(a,v,e,t);
  float sample=0;
  refuses([&]{s.feed(1,&sample,1);},"gap admitted");
  check(s.status().received==0,"bad admission mutated input");
  s.synthesize(1,"A complete reply.");
  refuses([&]{s.playback(1,0,true,false);},"premature drained accepted");
  until([&]{return !s.status().synthesizing;});
  Audio audio; uint64_t delivered=0; bool ended=false;
  while(s.audio(audio)) { if(audio.end) ended=true; else delivered+=audio.pcm.size(); }
  check(ended && delivered==1920,"complete tail missing");
  refuses([&]{s.playback(1,1921,true,false);},"invented render accepted");
  s.finish_input(0); until([&]{return s.status().input_finished;});
  s.close(false);
  check(!s.wait_closed(20),"drain completed without actual receipt");
  s.playback(1,delivered,true,false);
  check(s.wait_closed(1000),"receipt did not release drain");
  check(!s.status().aborted && s.status().error.empty(),"drain reported abort/failure");
  refuses([&]{s.playback(1,delivered,true,true);},"terminal outcome changed");
  Event event; size_t finals=0;
  while(s.event(event)) finals+=event.kind=="transcript_final";
  check(finals==0,"silence invented transcript");
}
void settled_generation_custody(){
  Asr a;Detector v;End e;Tts t;Session s(a,v,e,t);
  s.synthesize(1,"Settlement.");
  refuses([&]{s.release_generation(1);},"live generation released");
  until([&]{return !s.status().synthesizing;});
  Audio audio;uint64_t samples=0;while(s.audio(audio))samples+=audio.pcm.size();
  refuses([&]{s.release_generation(1);},"receipt debt released");
  s.playback(1,samples,true,false);
  refuses([&]{s.release_generation(1);},"unconsumed events released");
  Event event;while(s.event(event)){}
  s.release_generation(1);
  refuses([&]{s.generation(1);},"released job still retained");
  refuses([&]{s.synthesize(1,"Replay.");},"monotonic fence forgotten");
  s.synthesize(2,"Next.");s.close(true);check(s.wait_closed(1000),"owners did not retire");
}
void independent_interruption(uint32_t capture_minutes=default_capture_limit_minutes) {
  Asr a; a.released=false; Detector v; End e; Tts t;
  Settings settings;settings.capture_limit_minutes=capture_minutes;
  Session s(a,v,e,t,settings);
  std::vector<float> speech(512,.5f);
  check(s.feed(0,speech.data(),512),"first audio refused");
  until([&]{return a.entered.load();});
  std::vector<float> overload(32768,.5f);
  check(!s.feed(512,overload.data(),overload.size()),"unbounded input admitted behind blocked recognizer");
  check(s.status().received==512,"backpressure consumed refused input");
  t.hold=true; s.synthesize(1,"Keep speaking while recognition is delayed.");
  until([&]{return t.entered.load();});
  Audio frame; until([&]{return s.audio(frame);});
  until([&]{return t.blocked.load();});
  check(!frame.end && frame.pcm.size()==960,"no in-flight TTS audio");
  const auto start=std::chrono::steady_clock::now();
  check(s.feed(512,speech.data(),512),"interrupting input refused");
  until([&]{return t.through.load()==1;});
  check(std::chrono::steady_clock::now()-start<500ms,"cancel waited behind recognition");
  check(!a.released,"test accidentally released blocked recognizer");
  a.released=true;
  s.finish_input(1024);
  until([&]{return s.status().input_finished && !s.status().synthesizing;});
  bool ended=false;
  while(s.audio(frame)) {
    check(frame.pcm.empty(),"stale PCM escaped the interruption fence");
    ended|=frame.end;
  }
  check(ended,"cancel lost terminal audio boundary");
  s.playback(1,960,true,true);
  t.hold=false; s.synthesize(2,"Recovery reply.");
  until([&]{return !s.status().synthesizing;});
  uint64_t samples=0;
  while(s.audio(frame)) if(!frame.end) samples+=frame.pcm.size();
  check(samples==1920,"fresh generation could not recover");
  s.playback(2,samples,true,false); s.close(false);
  check(s.wait_closed(1000),"interrupted session owners did not retire");
  check(s.status().error.empty(),"expected cancellation faulted session");
  check(a.samples==1024,"opening words or exact input tail were lost");
}
std::vector<uint64_t> pause(size_t packet) {
  Asr a; Detector v; End e; Tts t; Session s(a,v,e,t,Settings{512,.5f});
  std::vector<float> pcm(1024,.5f); pcm.resize(1024+16000,0);
  send(s,pcm,packet); s.finish_input(pcm.size());
  until([&]{return s.status().input_finished;});
  std::vector<uint64_t> points; Event event;
  while(s.event(event)) if(event.kind=="turn_committed") {
    check(event.text=="semantic_pause","audio pause did not commit semantically"); points.push_back(event.end);
  }
  check(s.status().received==pcm.size() && s.status().recognized==pcm.size(),"finish tail clock changed");
  s.close(false); check(s.wait_closed(1000),"pause session retirement failed");
  check(s.status().error.empty(),"pause failed"); return points;
}
void abort_retirement() {
  Asr a; Detector v; End e; Tts t; t.hold=true;
  Session s(a,v,e,t); s.synthesize(1,"Cancel this.");
  until([&]{return t.entered.load();});
  s.close(true); check(s.wait_closed(1000),"Abort did not retire all owners");
  check(s.status().aborted,"Abort misreported as drain");
  Audio frame; check(!s.audio(frame),"Abort leaked queued output");
}
void every_unrendered_generation_is_fenced() {
  Asr a; Detector v; End e; Tts t; Session s(a,v,e,t);
  s.synthesize(1,"First queued reply."); until([&]{return !s.status().synthesizing;});
  s.synthesize(2,"Second queued reply."); until([&]{return !s.status().synthesizing;});
  std::vector<float> speech(512,.5f); check(s.feed(0,speech.data(),512),"speech refused");
  until([&]{return s.status().controlled==512;});
  Audio frame; size_t ends=0;
  while(s.audio(frame)) {
    check(frame.end && frame.pcm.empty(),"old queued generation escaped barge-in fence");
    s.playback(frame.generation,0,true,true); ++ends;
  }
  check(ends==2,"barge-in lost a generation boundary");
  s.finish_input(512);until([&]{return s.status().input_finished;});s.close(false);
  check(s.wait_closed(1000),"multiple-generation retirement failed");
}
void faults_are_not_cancelled() {
  Asr a; Detector v; End e; Tts t; t.hold=true; t.fault_on_cancel=true;
  Session s(a,v,e,t);s.synthesize(1,"Fault versus cancellation.");
  Audio frame;until([&]{return s.audio(frame);});until([&]{return t.blocked.load();});s.interrupt(1);
  check(s.wait_closed(1000),"faulted owners did not retire");
  check(s.status().error=="inference failure, not cancellation","fence hid a real inference failure");
}
void continuous_synthesis() {
  Asr a;Detector v;End e;Tts t;Session s(a,v,e,t);
  std::string text;
  for(int i=0;i<80;++i)text+="Café words stay whole. ";
  const auto parts=split_text(text);
  s.synthesize(1,text);until([&]{return !s.status().synthesizing;});
  const auto state=s.status();
  check(state.synthesis_segments==parts.size() && state.completed_segments==parts.size(),"long reply silently skipped a segment");
  check(t.texts.size()==parts.size(),"wrong synthesis segment census");
  for(size_t i=0;i<parts.size();++i)check(t.texts[i]==strip_text(parts[i]),"synthesis lost or duplicated text");
  Audio out;uint64_t samples=0;size_t ends=0;
  while(s.audio(out)) {
    check(out.generation==1 && out.start==samples,"segment reset the public output clock");
    if(out.end)++ends;else samples+=out.pcm.size();
  }
  check(ends==1 && samples==1920*parts.size(),"long reply lost its one complete tail");
  s.playback(1,samples,true,false);s.finish_input(0);until([&]{return s.status().input_finished;});
  s.close(false);check(s.wait_closed(1000),"continuous synthesis owners not retired");
}
void segmented_cancellation() {
  Asr a;Detector v;End e;Tts t;t.hold_segment=2;Session s(a,v,e,t);
  std::string text;for(int i=0;i<90;++i)text+="Preserve every word. ";
  s.synthesize(1,text);until([&]{return t.blocked.load();});s.interrupt(1);
  until([&]{return !s.status().synthesizing;});
  check(t.starts==2 && s.status().completed_segments==1,"cancel started a later segment or invented completion");
  Audio out;size_t ends=0;while(s.audio(out)){check(out.pcm.empty(),"cancelled segment PCM escaped");ends+=out.end;}
  check(ends==1,"segmented cancel lost terminal boundary");s.playback(1,0,true,true);
  s.synthesize(2,"Fresh recovery.");until([&]{return !s.status().synthesizing;});
  uint64_t samples=0;while(s.audio(out))if(!out.end)samples+=out.pcm.size();
  check(samples==1920,"segmented cancellation poisoned recovery");s.playback(2,samples,true,false);
  s.finish_input(0);until([&]{return s.status().input_finished;});s.close(false);check(s.wait_closed(1000),"segmented recovery retirement failed");
}
void output_bound_is_session_wide() {
  Asr a;Detector v;End e;Tts t;Session s(a,v,e,t);
  std::string text;for(int i=0;i<1300;++i)text+="Word. ";
  s.synthesize(1,text);until([&]{return !s.status().synthesizing;});
  s.synthesize(2,text);until([&]{return s.status().queued_audio_samples>=120000;});
  check(s.status().queued_audio_samples==120000 && s.status().synthesizing,"output bound multiplied by generations");
  s.interrupt(1);s.interrupt(2);until([&]{return !s.status().synthesizing;});
  check(s.status().queued_audio_samples==0,"fence did not release session output budget");
  Audio out;while(s.audio(out)){check(out.end,"stale queued reply returned");s.playback(out.generation,0,true,true);}
  s.finish_input(0);until([&]{return s.status().input_finished;});s.close(false);check(s.wait_closed(1000),"budget fence stranded workers");
}
void stop_is_not_cancel() {
  Asr a;Detector v;End e;Tts t;t.hold=true;Session s(a,v,e,t);
  s.synthesize(1,"Stop playback without pretending inference retired.");until([&]{return t.blocked.load();});
  s.stop_playback(1);
  auto g=s.generation(1);
  check(g.fenced && !g.cancelled && !g.retired && t.through==0,"stop incorrectly cancelled synthesis");
  check(s.status().synthesizing && s.status().queued_audio_samples==0,"stop lied about compute or kept stale audio");
  s.playback(1,0,true,true); // terminal browser stop may beat inference retirement
  t.release=true;until([&]{return !s.status().synthesizing;});
  g=s.generation(1);check(g.generated==1920 && !g.cancelled && g.receipt,"stop lost natural compute or early receipt");
  s.finish_input(0);s.close(false);check(!s.wait_closed(20),"close lost unresolved END");
  Audio out;until([&]{return s.audio(out);});check(out.end && out.start==0 && out.pcm.empty(),"stopped PCM revived");
  check(s.wait_closed(1000),"early receipt plus END did not release drain");
  Event event;bool ended=false,cancelled=false;while(s.event(event)){
    ended|=event.kind=="synthesis_end";cancelled|=event.kind=="synthesis_cancelled";
  }
  check(ended && !cancelled,"stop misreported synthesis as cancelled");
}
void cancel_after_stop_and_receipt() {
  Asr a;Detector v;End e;Tts t;t.hold=true;Session s(a,v,e,t);
  s.synthesize(1,"Keep computing until separately cancelled.");until([&]{return t.blocked.load();});
  s.stop_playback(1);s.playback(1,0,true,true);s.cancel_synthesis(1);
  until([&]{return !s.status().synthesizing;});
  check(t.through==1 && s.generation(1).cancelled,"early stop receipt disabled compute cancellation");
  Audio out;until([&]{return s.audio(out);});check(out.end,"cancel missing END");
  s.finish_input(0);s.close(false);check(s.wait_closed(1000),"separate stop/cancel retirement failed");
}
void future_finish_and_drain() {
  Asr a;a.released=false;Detector v;End e;Tts t;Session s(a,v,e,t);
  s.finish_input(1025);s.finish_input(1025);s.close(false);
  auto snapshot=s.status();check(snapshot.cutoff_set && snapshot.cutoff==1025 && snapshot.closing && !snapshot.input_finished,"future Finish invented completion");
  refuses([&]{s.finish_input(1026);},"changed cutoff accepted");
  refuses([&]{s.synthesize(1,"Too late");},"drain admitted new synthesis");
  std::vector<float> pcm(1025,.5f);send(s,pcm,241);
  check(s.status().received==1025 && !s.status().input_finished,"tail was lost or unprocessed input declared finished");
  refuses([&]{s.feed(1025,pcm.data(),1);},"audio past Finish cutoff admitted");
  a.released=true;check(s.wait_closed(1000),"future tail did not release drain");
  check(a.samples==1025 && s.status().recognized==1025 && s.status().input_finished,"future tail lost opening words or padding became input");
  Event event;size_t finals=0,finished=0;uint64_t final_seq=0;
  while(s.event(event)){
    if(event.kind=="transcript_final"){++finals;final_seq=event.sequence;}
    if(event.kind=="input_finished"){++finished;check(event.sequence>final_seq && event.end==1025 && event.start==1025,"completion preceded final or changed clock");}
  }
  check(finals==1 && finished==1,"future Finish minted duplicate or absent evidence");
}
void missing_tail_is_failure() {
  Asr a;Detector v;End e;Tts t;Session s(a,v,e,t,Settings{768,.5f,20});
  s.finish_input(2048);std::vector<float> pcm(512,.5f);check(s.feed(0,pcm.data(),pcm.size()),"tail prefix refused");
  s.finish_input(2048);s.close(false);
  check(s.wait_closed(1000),"missing tail did not retire within its deadline");
  check(s.status().error=="input tail missing at admitted cutoff" && !s.status().input_finished,"missing tail was silently completed");
  Event event;while(s.event(event))check(event.kind!="input_finished","missing tail minted successful input completion");
}
void backpressured_tail_is_not_missing(bool deliver) {
  Asr a;a.released=false;Detector v;End e;Tts t;Session s(a,v,e,t,Settings{768,.5f,200});
  std::vector<float> pcm(32768,.5f);
  check(s.feed(0,pcm.data(),pcm.size()),"initial bounded queue refused");
  until([&]{return a.entered.load();});
  s.finish_input(32769);
  check(!s.feed(32768,pcm.data(),1),"blocked recognizer did not apply backpressure");
  std::this_thread::sleep_for(600ms);
  check(s.status().error.empty() && !s.status().input_finished,
        "engine backpressure was called missing input");
  // Polling and duplicate Finish do not renew a missing-tail budget.
  s.finish_input(32769);
  a.released=true;
  // Retry when capacity first returns, not after draining the entire queue.
  // Waiting for all recognition spends the transport budget on test work.
  until([&]{return s.status().recognized>=512;});
  if(deliver) {
    check(s.feed(32768,pcm.data(),1),"already-present tail was refused after capacity returned");
    s.close(false);check(s.wait_closed(1000),"backpressured tail did not drain");
    check(a.samples==32769 && s.status().input_finished,"backpressure lost a tail sample");
  } else {
    s.close(false);check(s.wait_closed(1000),"missing tail deadline never resumed");
    check(s.status().error=="input tail missing at admitted cutoff" && !s.status().input_finished,
          "capacity return waived a truly missing tail");
  }
}
void abort_bypasses_backpressured_tail() {
  Asr a;a.released=false;Detector v;End e;Tts t;Session s(a,v,e,t,Settings{768,.5f,200});
  std::vector<float> pcm(32768,.5f);
  check(s.feed(0,pcm.data(),pcm.size()),"initial queue refused");
  until([&]{return a.entered.load();});s.finish_input(32769);
  check(!s.feed(32768,pcm.data(),1),"tail was not backpressured");
  s.close(true);check(s.wait_closed(1000) && a.cancelled,"Abort waited behind the held tail");
  check(!s.status().input_finished,"Abort fabricated a completed tail");
}
void bounded_read_keeps_custody() {
  Asr a;Detector v;End e;Tts t;Session s(a,v,e,t);s.synthesize(1,"Preserve queued output on capacity refusal.");
  until([&]{return !s.status().synthesizing;});
  Event event;size_t required=0;check(!s.event_bounded(event,0,required) && required==1,"event capacity refusal lost event");
  check(s.event(event) && event.kind=="synthesis_start","event retry skipped first event");
  Audio out;check(!s.audio_bounded(out,0,required) && required==960,"audio capacity refusal lost size");
  check(s.generation(1).delivered==0,"refused audio was marked delivered");
  s.stop_playback(1);check(s.audio_bounded(out,0,required) && out.end,"capacity retry revived detached stale PCM");
  s.playback(1,0,true,true);s.finish_input(0);s.close(false);check(s.wait_closed(1000),"bounded read stranded owner");
}
int main() {
  try {
    settled_generation_custody();
    admission_and_receipts(); std::cout<<"receipt-held drain and exact terminal evidence PASS\n";
    independent_interruption(); std::cout<<"interruption bypasses blocked recognition; prefix and recovery PASS\n";
    independent_interruption(0);std::cout<<"unlimited duration retains bounded backpressure and independent interruption PASS\n";
    const auto a=pause(512),b=pause(241),c=pause(4096);
    check(a.size()==1 && a==b && a==c && a[0]==9216,"pause commitment depends on transport batch");
    std::cout<<"512ms pause at sample 9216 across 512/241/4096 input batches PASS\n";
    abort_retirement(); std::cout<<"Abort admission and owner retirement PASS\n";
    every_unrendered_generation_is_fenced();std::cout<<"barge-in fences every unrendered generation PASS\n";
    faults_are_not_cancelled();std::cout<<"inference failure remains visible behind a cancellation fence PASS\n";
    continuous_synthesis();std::cout<<"lossless long reply, one generation/clock/END PASS\n";
    segmented_cancellation();std::cout<<"cancel between reply segments preserves recovery PASS\n";
    output_bound_is_session_wide();std::cout<<"bounded output is shared across all queued generations PASS\n";
    stop_is_not_cancel();std::cout<<"playback stop is independent of compute and early receipt is retained PASS\n";
    cancel_after_stop_and_receipt();std::cout<<"stop/receipt cannot disable later compute cancellation PASS\n";
    future_finish_and_drain();std::cout<<"future Finish and early drain preserve exact tail and opening words PASS\n";
    missing_tail_is_failure();std::cout<<"missing input tail faults rather than completing PASS\n";
    backpressured_tail_is_not_missing(true);backpressured_tail_is_not_missing(false);
    abort_bypasses_backpressured_tail();
    std::cout<<"backpressure suspends only the missing-tail wait; deadline resumes on capacity PASS\n";
    bounded_read_keeps_custody();std::cout<<"capacity refusal keeps event/audio custody under control fences PASS\n";
  } catch(const std::exception& e) { std::cerr<<e.what()<<'\n'; return 1; }
}
