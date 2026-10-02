#include "c_api_internal.h"
#include "../../native_multitalker/separating_recognizer.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <iostream>
#include <mutex>
#include <thread>

using namespace aii::voice;
namespace {
void check(bool ok,const char* reason){if(!ok)throw std::runtime_error(reason);}
struct Asr final:Recognizer {
  size_t samples=0,begins=0,total=0;bool malformed=false,continuous=false,selected=false;
  int region_case=0;
  std::string unavailable;
  bool contradict_evidence=false;
  void begin() override {samples=0;++begins;}
  std::string push(const float*,size_t count) override {samples+=count;total+=count;return {};}
  std::string finish() override {return {};}
  bool separated() const override {return true;}
  bool continuous_input() const override {return continuous;}
  std::vector<RecognizedSegment> segments() const override {
    if(selected) {
      std::vector<RecognizedSegment> rows{{"utterance-1.track-0","first speaker's words",0,samples,0,std::vector<float>(32000,.25f)},
                        {"utterance-1.track-1","second speaker's words",0,samples,32000,std::vector<float>(32000,.75f)}};
      if(region_case)rows[0].evidence_regions={{0,16000},{32000,48000}};
      if(region_case==2)rows[0].evidence_regions[1]={15000,31000};
      if(region_case==3)rows[0].evidence_regions[1].second=47999;
      if(region_case==4)rows[0].evidence_regions[1]={samples,samples+16000};
      if(region_case==5)rows[0].evidence_regions[0].first=1;
      if(region_case==6)rows[0].evidence.clear();
      return rows;
    }
    std::vector<RecognizedSegment> rows{{"utterance-1.track-0","first speaker's words",0,samples},
            {"utterance-1.track-1","second speaker's words",samples/2,malformed?samples+1:samples}};
    for(auto& row:rows) {
      row.evidence_unavailable=unavailable;
      if(contradict_evidence)row.evidence.push_back(.3f);
    }
    return rows;
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
  uint64_t previous_utterance=0;
  std::atomic<size_t> calls{0};
  std::atomic<size_t> tracks{0};std::atomic<bool> hold{false},cancelled{false};
  std::string identify(uint64_t,const std::vector<float>&)override{++calls;return "{}";}
  std::string identify_track_at(uint64_t final,uint64_t utterance,const std::vector<float>& pcm)override {
    check(utterance>0,"missing private utterance origin");
    if(previous_utterance)check(previous_utterance==utterance,"sibling tracks claimed independent utterances");
    previous_utterance=utterance;
    return identify_track(final,pcm);
  }
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
  size_t observations=0,finals=0;Event event;
  while(session.event(event)) {
    if(event.kind=="transcript_final") {
      check(event.track=="utterance-1.track-"+std::to_string(finals++),
            "pooled recognizer text published beside separated finals");
    }
    if(event.kind=="speaker_observation") {
      check(event.track=="utterance-1.track-"+std::to_string(observations++),"asynchronous track binding changed");
      check(event.clean_track && event.evidence_start==(observations==1?0u:32000u) &&
            event.evidence_end==(observations==1?32000u:64000u),
            "validated separated evidence did not accompany speaker result");
    }
  }
  check(finals==2,"separated final census changed");
  check(observations==(abort?0u:2u),"track observation census");
}
void selected_region_contract(int variant) {
  Asr a;a.selected=true;a.region_case=variant;V v;E e;T t;S speaker;
  Session session(a,v,e,t,Settings{5000,.5f},&speaker);
  const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5);
  std::vector<float> pcm(512,.5f);
  for(uint64_t offset=0;offset<64000;offset+=512)while(!session.feed(offset,pcm.data(),512)) {
    check(std::chrono::steady_clock::now()<deadline,"region feed deadline");std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  session.finish_input(64000);
  while(!session.status().input_finished&&session.status().error.empty()) {
    check(std::chrono::steady_clock::now()<deadline,"region finish deadline");std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  check((variant>1)==!session.status().error.empty(),"invalid evidence regions admitted or valid regions refused");
  session.close(variant>1);check(session.wait_closed(2000),"region retirement");
  check(speaker.tracks==(variant>1?0u:2u),"invalid region PCM reached UID or valid evidence lost");
  if(variant<=1) {
    Event event;bool found=false;
    while(session.event(event))if(event.kind=="speaker_observation" && event.track=="utterance-1.track-0") {
      check(event.clean_track && event.evidence_start==0 && event.evidence_end==(variant?48000u:32000u),
            "selected evidence regions lost their original clock span");found=true;
    }
    check(found,"selected track observation missing");
  }
}
void unavailable_does_not_run_matcher(const std::string& reason,bool invalid=false,bool contradiction=false) {
  Asr a;a.unavailable=reason;a.contradict_evidence=contradiction;V v;E e;T t;S speaker;speaker.hold=true;
  Session session(a,v,e,t,Settings{5000,.5f},&speaker);
  std::vector<float> pcm(4096,.3f);
  check(session.feed(0,pcm.data(),pcm.size()),"unavailable input admitted");
  session.finish_input(pcm.size());session.close(false);
  check(session.wait_closed(2000),"missing evidence waited behind the matcher");
  check(invalid==!session.status().error.empty(),"invalid selection reason was admitted");
  check(!speaker.calls && !speaker.tracks,"missing evidence invoked the matcher");
  size_t finals=0,observations=0;uint64_t final=0;Event event;
  while(session.event(event)) {
    if(event.kind=="transcript_final"){++finals;final=event.sequence;}
    if(event.kind=="speaker_observation") {
      ++observations;
      check(event.refers_to==final && !event.clean_track,"unavailable final binding");
      check(event.text.find(reason)!=std::string::npos,"selection reason lost");
      for(const auto* forbidden:{"speaker_uuid","score","margin","pcm_sha256"})
        check(event.text.find(forbidden)==std::string::npos,"missing evidence invented identity evidence");
    }
  }
  check(finals==(invalid?0u:2u)&&observations==finals,"missing evidence lost or duplicated words");
}
}
// The resident separating composition behind Session, with model doubles.
struct Mixed final:Recognizer {
  std::vector<float> pcm;
  void begin() override {pcm.clear();}
  std::string push(const float* p,size_t n) override {pcm.insert(pcm.end(),p,p+n);return {};}
  std::string finish() override {return {};}
  bool separated() const override {return true;}
  bool continuous_input() const override {return true;}
  std::vector<RecognizedSegment> segments() const override {
    std::vector<RecognizedSegment> rows;
    for(size_t i=0;i<2;++i) {
      rows.push_back({"utterance-1.track-"+std::to_string(i),"mixed words",0,pcm.size()});
      rows.back().evidence_unavailable="speaker_overlap_without_isolated_evidence";
    }
    return rows;
  }
  void reset() override {pcm.clear();}
  void cancel() noexcept override {}
};
struct Source final:Recognizer {
  std::vector<float> pcm;size_t failures=0;
  void begin() override {pcm.clear();}
  std::string push(const float* p,size_t n) override {
    if(failures){--failures;throw std::runtime_error("source inference failed under a private path");}
    pcm.insert(pcm.end(),p,p+n);return {};
  }
  std::string finish() override {return {};}
  bool separated() const override {return true;}
  std::vector<RecognizedSegment> segments() const override {
    return {{"track-0",pcm.front()>0?"first source":"second source",0,pcm.size(),0,pcm}};
  }
  void reset() override {pcm.clear();}
  void cancel() noexcept override {}
};
struct Separator final:aii::multitalker::SourceSeparator {
  std::vector<std::vector<float>> inputs;size_t failures=0,stalls=0;
  std::mutex mutex;std::condition_variable changed;bool cancelled=false;
  size_t maximum_samples() const override {return 80003;}
  std::string provider() const override {return "test";}
  void open() override {std::lock_guard<std::mutex> lock(mutex);cancelled=false;}
  aii::multitalker::Waveforms separate(const std::vector<float>& pcm) override {
    inputs.push_back(pcm);
    if(failures){--failures;throw std::runtime_error("device out of memory under a private path");}
    std::unique_lock<std::mutex> lock(mutex);
    if(stalls){--stalls;changed.wait(lock,[&]{return cancelled;});} // a throttled run, until cancelled
    if(cancelled)throw Cancelled("separator cancelled");
    return {{std::vector<float>(pcm.size(),.125f),std::vector<float>(pcm.size(),-.25f)}};
  }
  void cancel() noexcept override {{std::lock_guard<std::mutex> lock(mutex);cancelled=true;}changed.notify_all();}
};
struct Tracks final:SpeakerIdentifier {
  std::mutex mutex;std::vector<std::vector<float>> pcm;
  std::string identify(uint64_t,const std::vector<float>&) override {throw std::runtime_error("pooled identity");}
  std::string identify_track_at(uint64_t,uint64_t,const std::vector<float>& p) override {
    std::lock_guard<std::mutex> lock(mutex);pcm.push_back(p);
    return R"({"outcome":"unavailable","reason":"fixture_track","used_for_permissions":false})";
  }
  void cancel() noexcept override {}
};
struct Composition {
  Separator* separator=new Separator;Source* source=new Source;
  aii::multitalker::SeparatingRecognizer asr;
  V v;E e;T t;Tracks tracks;std::vector<Event> events;uint32_t model_call_ms=30000;
  explicit Composition(aii::multitalker::SeparationBudget budget={})
      :asr(std::make_unique<Mixed>(),std::unique_ptr<Source>(source),std::unique_ptr<Separator>(separator),budget) {}
  // Speech is any block whose first sample exceeds VAD's 0.1. Every sample is
  // position-coded so a separator window can be compared with the capture.
  void run(const std::vector<std::pair<size_t,bool>>& spans,uint32_t pause_ms,std::vector<float>& pcm) {
    for(const auto& span:spans)for(size_t i=0;i<span.first*512;++i)
      pcm.push_back((span.second?.3f:0.f)+float(pcm.size()%1000)/16384);
    Settings settings{pause_ms,.5f};settings.model_call_timeout_ms=model_call_ms;
    Session session(asr,v,e,t,settings,&tracks);
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(10);
    for(size_t at=0;at<pcm.size();at+=512)for(;;) {
      const auto error=session.status().error;
      if(!error.empty())throw std::runtime_error("separating session fault: "+error);
      if(session.feed(at,pcm.data()+at,512))break;
      check(std::chrono::steady_clock::now()<deadline,"separating feed deadline");
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    session.finish_input(pcm.size());session.close(false);
    check(session.wait_closed(5000),"separating session retirement");
    check(session.status().error.empty(),session.status().error.c_str());
    Event event;while(session.event(event))events.push_back(event);
  }
};
// The bounded separator window starts at the turn's VAD preroll (31 earlier
// blocks), not at the end of the previous turn: leading silence cannot spend it.
void leading_silence(size_t silence_blocks,size_t speech_blocks) {
  Composition c;std::vector<float> pcm;c.run({{silence_blocks,false},{speech_blocks,true}},5000,pcm);
  const uint64_t origin=silence_blocks>31?(silence_blocks-31)*512:0,total=pcm.size();
  const bool fits=total-origin<=80003;
  check(c.separator->inputs.size()==(fits?1u:0u),"leading silence decided separation");
  if(fits)check(c.separator->inputs[0]==std::vector<float>(pcm.begin()+origin,pcm.end()),"separator window is not the turn");
  size_t finals=0,observations=0;
  for(const auto& event:c.events) {
    if(event.kind=="transcript_final") {
      check(event.text==(fits?(finals?"second source":"first source"):"mixed words"),"separated text lost");
      check(event.start==(fits?origin:0)&&event.end==total,"separated final left the capture clock");++finals;
    }
    if(event.kind=="speaker_observation") {
      check(event.clean_track==fits,"separated evidence lost");
      if(fits)check(event.evidence_start==origin&&event.evidence_end==total,"identity evidence left the capture clock");
      else check(event.text.find("speaker_overlap_without_isolated_evidence")!=std::string::npos,"unresolved reason lost");
      ++observations;
    }
  }
  check(finals==2&&observations==2,"separated final census");
  check(c.tracks.pcm.size()==(fits?2u:0u),"identity evidence census");
  for(size_t i=0;i<c.tracks.pcm.size();++i)
    check(c.tracks.pcm[i]==std::vector<float>(total-origin,i?-.25f:.125f),"identity PCM is not its source");
}
// A second turn's preroll is counted inside its own capture, after the first
// turn committed at a pause; both turns keep their absolute clock. The gaps
// exceed the pause gate's acoustic maximum, so a late endpoint still commits.
void separated_turns() {
  Composition c;std::vector<float> pcm;c.run({{94,false},{78,true},{125,false},{78,true}},320,pcm);
  std::vector<Event> finals;
  for(const auto& event:c.events)if(event.kind=="transcript_final")finals.push_back(event);
  check(c.separator->inputs.size()==2&&finals.size()==4,"each turn not separated once");
  const uint64_t onsets[]={(94-31)*512,(94+78+125-31)*512};
  for(size_t turn=0;turn<2;++turn) {
    const auto& final=finals[2*turn];
    check(final.start==onsets[turn]&&finals[2*turn+1].start==final.start&&final.end==finals[2*turn+1].end,
      "turn window does not start at its preroll");
    check(c.separator->inputs[turn]==std::vector<float>(pcm.begin()+final.start,pcm.begin()+final.end),
      "separator window and final clock differ");
  }
  check(finals[3].end==pcm.size(),"second turn lost its end");
}
// A separator or source model that fails to run keeps the completed live
// transcript with a typed unresolved observation. The session continues, the
// next turn separates, and no model message reaches an event or the status.
void separation_failure(bool in_source) {
  Composition c;(in_source?c.source->failures:c.separator->failures)=1;
  std::vector<float> pcm;c.run({{15,false},{78,true},{100,false},{78,true}},320,pcm);
  std::vector<Event> finals,observations;
  for(const auto& event:c.events) {
    check(event.text.find("private")==std::string::npos,"model failure message published");
    if(event.kind=="transcript_final")finals.push_back(event);
    if(event.kind=="speaker_observation")observations.push_back(event);
  }
  check(c.separator->inputs.size()==2&&finals.size()==4&&observations.size()==4,"failed turn lost a final");
  check(finals[0].text=="mixed words"&&finals[1].text=="mixed words"&&finals[0].start==0&&
    finals[2].text=="first source"&&finals[3].text=="second source","failed turn did not keep its transcript");
  for(size_t i=0;i<2;++i)check(!observations[i].clean_track&&observations[i].refers_to==finals[i].sequence&&
    observations[i].text.find(R"("reason":"speaker_separation_failed")")!=std::string::npos,"separation failure not observed");
  check(observations[2].clean_track&&observations[3].clean_track&&c.tracks.pcm.size()==2,"next turn did not separate");
}
// A separator still running at its latency budget (a throttled CPU) is
// cancelled long before the model-call watchdog: the turn keeps its live
// transcript, the session continues and the re-armed separator serves the
// next turn. The cause is visible in the composition's execution report.
void separation_budget() {
  Composition c({250,std::chrono::milliseconds(300),std::chrono::milliseconds(300)});
  c.separator->stalls=1;c.model_call_ms=2000;
  std::vector<float> pcm;c.run({{15,false},{78,true},{100,false},{78,true}},320,pcm);
  std::vector<Event> finals,observations;
  for(const auto& event:c.events) {
    if(event.kind=="transcript_final")finals.push_back(event);
    if(event.kind=="speaker_observation")observations.push_back(event);
  }
  check(c.separator->inputs.size()==2&&finals.size()==4&&observations.size()==4,"expired turn lost a final");
  check(finals[0].text=="mixed words"&&finals[1].text=="mixed words"&&finals[2].text=="first source",
    "expired separation did not keep the live transcript");
  for(size_t i=0;i<2;++i)check(observations[i].text.find(R"("reason":"speaker_separation_failed")")!=std::string::npos,
    "expired separation not observed");
  const auto info=c.asr.execution_info();
  check(info.find(R"("budget_expired":1)")!=std::string::npos&&info.find(R"("replaced":1)")!=std::string::npos&&
    info.find(R"("last_separation_outcome":"replaced")")!=std::string::npos,"separation outcomes not reported");
}
int main(){try{run(false);run(true);capture_origin(false);capture_origin(true);capture_across_pause();
  for(size_t silence:{15u,62u,94u,313u})leading_silence(silence,78);
  leading_silence(94,172);separated_turns();separation_failure(false);separation_failure(true);separation_budget();selected_uid_queue(false);selected_uid_queue(true);for(int i=1;i<=6;++i)selected_region_contract(i);
  for(const auto* reason:{"speaker_overlap_without_isolated_evidence","speaker_activity_uncertain",
      "speaker_evidence_too_short","speaker_evidence_expired","speaker_activity_unavailable",
      "speaker_track_coverage_unverified","speaker_separation_failed"})
    unavailable_does_not_run_matcher(reason);
  unavailable_does_not_run_matcher("unvalidated reason",true);
  unavailable_does_not_run_matcher("speaker_evidence_too_short",true,true);
  return 0;}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
