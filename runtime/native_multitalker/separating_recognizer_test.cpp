#include "separating_recognizer.h"
#include "onnx_separator.h"
#include <chrono>
#include <condition_variable>
#include <functional>
#include <iostream>
#include <limits>
#include <mutex>
#include <thread>

namespace {
static_assert(aii::multitalker::OnnxSeparator::input_limit==80003,
    "resident separator must retain the qualified resource window");
using aii::voice::RecognizedSegment;
using aii::multitalker::Waveforms;
void check(bool ok,const char* why){if(!ok)throw std::runtime_error(why);}
template<class F>void refuses(F f,const char* why) {
  bool failed=false;try{f();}catch(const std::exception&){failed=true;}check(failed,why);
}
struct Recognition:aii::voice::Recognizer {
  bool live=false,competition=true,silent=false,pooled=false,regions=false,corrupt=false;
  size_t opens=0,begins=0,finishes=0,resets=0,tracks=2,finish_stages=0;
  uint64_t onset=std::numeric_limits<uint64_t>::max();
  std::string second_reason;
  std::atomic<size_t> cancels{0};
  std::vector<float> pcm;
  std::function<void()> on_finish,on_open,on_push;
  explicit Recognition(bool l):live(l){}
  void open() override {++opens;if(on_open)on_open();}
  void begin() override {++begins;pcm.clear();}
  std::string push(const float* p,size_t n) override {
    if(n)pcm.insert(pcm.end(),p,p+n);
    if(on_push)on_push();
    return pooled?"mixed":"";
  }
  std::string finish() override {++finishes;if(on_finish)on_finish();return {};}
  std::string finish_with_progress(const std::function<void()>& completed) override {
    auto text=finish();
    for(size_t i=0;i<finish_stages;++i)if(completed)completed();
    return text;
  }
  bool separated() const override {return true;}
  bool continuous_input() const override {return true;}
  void speech_onset(uint64_t offset) override {onset=offset;}
  std::vector<RecognizedSegment> segments() const override {
    if(silent||(!live&&!pcm.empty()&&pcm.front()==0))return {};
    std::vector<RecognizedSegment> rows;
    for(size_t i=0;i<(live?tracks:1);++i) {
      RecognizedSegment row;row.track="track-"+std::to_string(i);row.end=pcm.size();
      row.text=live?"original unresolved":(pcm.front()>0?"first source":"second source");
      if(live&&competition)row.evidence_unavailable=i&&!second_reason.empty()?second_reason:"speaker_overlap_without_isolated_evidence";
      else if(regions&&pcm.size()>=32000&&pcm.size()<=160000) {
        // Two original-clock islands, as a real selector may report them.
        row.evidence.assign(pcm.begin(),pcm.begin()+16000);
        row.evidence.insert(row.evidence.end(),pcm.end()-16000,pcm.end());
        row.evidence_regions={{0,16000},{pcm.size()-16000,pcm.size()}};
      }
      else if(pcm.size()>=32000&&pcm.size()<=160000)row.evidence=pcm;
      else row.evidence_unavailable="speaker_evidence_too_short";
      if(corrupt&&!row.evidence.empty())row.evidence[1]*=-1; // not the recognized waveform
      rows.push_back(row);
    }
    return rows;
  }
  void reset() override {++resets;pcm.clear();}
  void cancel() noexcept override {++cancels;}
};
// Like the native adapters: cancel() sets a flag that terminates the running
// call (block waits for it) and refuses later calls until open() re-arms it.
struct Separation:aii::multitalker::SourceSeparator {
  size_t calls=0,opens=0;
  std::atomic<size_t> cancels{0};
  bool empty_second=false,block=false,ignore_cancel=false;
  std::mutex mutex;std::condition_variable changed;bool cancelled=false;
  std::vector<float> input;
  std::function<void()> on_separate;
  std::function<void(Waveforms&)> alter;
  size_t maximum_samples() const override {return aii::multitalker::OnnxSeparator::input_limit;}
  std::string provider() const override {return "test";}
  void open() override {++opens;std::lock_guard<std::mutex> lock(mutex);cancelled=false;}
  Waveforms separate(const std::vector<float>& pcm) override {
    ++calls;input=pcm;if(on_separate)on_separate();
    {
      std::unique_lock<std::mutex> lock(mutex);
      if(block)changed.wait(lock,[&]{return cancelled;});
      if(cancelled&&!ignore_cancel)throw aii::voice::Cancelled("separator cancelled");
    }
    Waveforms sources{{std::vector<float>(pcm.size(),.125f),std::vector<float>(pcm.size(),empty_second?0:-.25f)}};
    if(alter)alter(sources);
    return sources;
  }
  void cancel() noexcept override {
    ++cancels;{std::lock_guard<std::mutex> lock(mutex);cancelled=true;}changed.notify_all();
  }
};
struct Fixture {
  Recognition* live;Recognition* source;Separation* separation;
  aii::multitalker::SeparatingRecognizer recognizer;
  explicit Fixture(aii::multitalker::SeparationBudget budget={}):live(new Recognition(true)),source(new Recognition(false)),
    separation(new Separation),recognizer(std::unique_ptr<Recognition>(live),std::unique_ptr<Recognition>(source),
    std::unique_ptr<Separation>(separation),budget){}
  // The composition's fixed diagnostic tokens, read as the worker's status does.
  std::string last() const {
    const auto info=recognizer.execution_info();const std::string key="\"last_separation_outcome\":\"";
    const auto at=info.find(key);check(at!=std::string::npos,"separation outcome not reported");
    return info.substr(at+key.size(),info.find('"',at+key.size())-at-key.size());
  }
  size_t count(const std::string& outcome) const {
    const auto info=recognizer.execution_info(),key="\""+outcome+"\":";
    const auto at=info.find(key);check(at!=std::string::npos,"separation outcome count missing");
    return std::stoul(info.substr(at+key.size()));
  }
  std::vector<RecognizedSegment> turn(size_t n=48000) {
    recognizer.begin();std::vector<float> pcm(n,.2f);recognizer.push(pcm.data(),n);
    check(recognizer.finish().empty(),"pooled finish");auto rows=recognizer.segments();recognizer.reset();return rows;
  }
  // Continuous capture in session-sized blocks: earlier silence, then a turn
  // whose onset preroll is named, as Session does. Samples are position-coded.
  std::vector<float> fed;
  std::vector<RecognizedSegment> announced(size_t silence,size_t preroll,size_t speech) {
    fed.assign(silence+speech,0);
    for(size_t i=0;i<fed.size();++i)fed[i]=(i<silence?0.f:.25f)+float(i%1000)/16384;
    recognizer.begin();
    for(size_t at=0;at<silence;at+=512)recognizer.push(fed.data()+at,std::min<size_t>(512,silence-at));
    recognizer.speech_onset(silence-std::min(silence,preroll));
    for(size_t at=silence;at<fed.size();at+=512)recognizer.push(fed.data()+at,std::min<size_t>(512,fed.size()-at));
    check(recognizer.finish().empty(),"pooled finish");auto rows=recognizer.segments();recognizer.reset();return rows;
  }
};
}
int main(){try{
  {
    Fixture progress;progress.recognizer.open();progress.recognizer.begin();
    std::vector<float> pcm(48000,.2f);progress.recognizer.push(pcm.data(),pcm.size());
    size_t stages=0;
    progress.recognizer.finish_with_progress([&]{++stages;});
    check(stages==4+2*((pcm.size()+996)/997),
      "progress must cover completed source calls as well as whole stages");
  }
  {
    Fixture progress;progress.live->finish_stages=2;progress.source->finish_stages=3;
    progress.recognizer.open();progress.recognizer.begin();
    std::vector<float> pcm(48000,.2f);progress.recognizer.push(pcm.data(),pcm.size());
    size_t stages=0;
    progress.recognizer.finish_with_progress([&]{++stages;});
    check(stages==4+2*((pcm.size()+996)/997)+2+2*3,
      "nested live and source refinement progress was discarded");
  }
  {
    Fixture progress;progress.live->finish_stages=1;
    progress.recognizer.open();progress.recognizer.begin();
    std::vector<float> pcm(48000,.2f);progress.recognizer.push(pcm.data(),pcm.size());
    refuses([&]{progress.recognizer.finish_with_progress([]{
      throw aii::voice::Cancelled("completed call exceeded its deadline");
    });},"progress refusal was swallowed");
    check(progress.separation->calls==0&&progress.source->begins==0,
      "work continued after a nested progress refusal");
    refuses([&]{progress.recognizer.segments();},"failed progress published a final");
  }
  {
    // Separation is best effort under a latency budget: 5 x the separated
    // audio, clamped to 4..25 s where its owner states no other bounds, and
    // inside the time its owner states for a model call.
    using aii::multitalker::SeparationBudget;using std::chrono::milliseconds;
    check(SeparationBudget::default_audio_percent==500&&SeparationBudget::default_minimum==milliseconds(4000)&&
      SeparationBudget::default_maximum==milliseconds(25000),"separation budget constants");
    const SeparationBudget production;
    check(production.of(80003)==milliseconds(25000)&&production.of(32000)==milliseconds(10000)&&
      production.of(16000)==milliseconds(5000)&&production.of(480000)==milliseconds(25000),"separation budget rule");
    for(const SeparationBudget& invalid:{SeparationBudget{0,milliseconds(4000),milliseconds(15000)},
        SeparationBudget{250,milliseconds(0),milliseconds(15000)},SeparationBudget{250,milliseconds(5000),milliseconds(4000)}})
      refuses([&]{Fixture refused(invalid);},"invalid separation budget admitted");
    // Its owner restates the two bounds before a turn, and the ratio stays.
    Fixture stated;
    refuses([&]{stated.recognizer.bound_separation(0,15000,30000);},"a budget with no least time admitted");
    refuses([&]{stated.recognizer.bound_separation(5000,4000,30000);},"a budget whose most is the less admitted");
    refuses([&]{stated.recognizer.bound_separation(4000,30000,30000);},"a budget as long as a model call admitted");
    refuses([&]{stated.recognizer.bound_separation(4000,25000,10000);},"a budget longer than the stated model call admitted");
    stated.recognizer.bound_separation(700,9000,9001);
    check(stated.recognizer.execution_info().find(R"("separation_budget":{"audio_percent":500,"minimum_ms":700,"maximum_ms":9000})")!=std::string::npos,
      "restated bounds not reported");
  }
  Fixture f;f.recognizer.open();
  check(f.last()=="none"&&f.count("replaced")==0,"separation outcome invented before a turn");
  auto rows=f.turn();
  check(f.last()=="replaced"&&f.count("replaced")==1,"replacement not reported");
  check(rows.size()==2&&rows[0].text=="first source"&&rows[1].text=="second source","source text not used");
  check(rows[0].evidence.front()==.125f&&rows[1].evidence.front()==-.25f,"text and PCM differ");
  check(f.live->opens==1&&f.source->opens==2,"source reset modified live history");
  const auto old_track=rows[0].track;rows=f.turn();
  check(rows[0].track!=old_track&&f.live->opens==1&&f.source->opens==4,"next turn reused origin or lost history");
  f.live->competition=false;f.live->tracks=1;rows=f.turn();
  check(rows.size()==1&&rows[0].text=="original unresolved"&&f.separation->calls==2,"clean turn unnecessarily separated");
  check(f.count("replaced")==2&&f.last()=="replaced","a turn without competition reported a separation outcome");
  f.live->competition=true;f.live->tracks=2;
  for(size_t n:{31999u,80004u,480001u}) {
    rows=f.turn(n);check(rows.size()==2&&rows[0].evidence.empty()&&f.separation->calls==2,
      "out of range turn truncated or attributed");
    check(f.last()==(n<32000?"below_minimum":"window_exceeded"),"skipped separation not explained");
  }
  rows=f.turn();check(f.separation->calls==3&&rows[0].text=="first source","overflow poisoned next turn");
  rows=f.turn(80003);check(f.separation->calls==4&&rows[0].text=="first source"&&rows[0].end==80003,
    "complete boundary-length sources not retained");
  f.live->tracks=3;rows=f.turn();check(rows.size()==3&&f.separation->calls==4,"third source forced into two channels");
  check(f.last()=="too_many_tracks","third track not explained");
  f.live->tracks=2;f.separation->empty_second=true;rows=f.turn();
  check(rows.size()==2&&rows[0].evidence.empty()&&rows[0].text=="original unresolved","one source lost silently");
  check(f.last()=="incomplete_sources"&&f.count("incomplete_sources")==1,"silent source not explained");
  f.separation->empty_second=false;
  {
    // Leading silence must not spend the bounded window: the turn is separated
    // from its named preroll, and each source binds to the original clock.
    for(size_t silence:{48000u,160000u}) {
      Fixture lead;lead.source->regions=true;lead.recognizer.open();
      const size_t preroll=15872,speech=40000,origin=silence-preroll,total=silence+speech;
      rows=lead.announced(silence,preroll,speech);
      check(lead.separation->calls==1&&lead.live->onset==origin,"leading silence disabled separation");
      check(lead.separation->input==std::vector<float>(lead.fed.begin()+origin,lead.fed.end()),
        "separator window is not the announced turn");
      check(rows.size()==2&&rows[0].text=="first source"&&rows[1].text=="second source","separated text lost");
      for(const auto& row:rows) {
        check(row.start==origin&&row.end==total&&row.evidence_start==origin&&row.evidence.size()==32000&&
          row.evidence_regions==std::vector<std::pair<uint64_t,uint64_t>>{{origin,origin+16000},{total-16000,total}},
          "separated text or identity PCM left the original clock");
      }
      check(rows[0].evidence.front()==.125f&&rows[1].evidence.back()==-.25f,"source evidence exchanged");
    }
    Fixture bound;bound.recognizer.open();
    rows=bound.announced(48000,15872,80003-15872);
    check(bound.separation->calls==1&&rows[0].text=="first source"&&rows[0].end-rows[0].start==80003,
      "announced turn at the qualified boundary not separated");
    // A turn longer than the window, even counted from its onset, and a preroll
    // that has already left the window keep their unresolved records.
    rows=bound.announced(48000,15872,80003-15872+1);
    check(bound.separation->calls==1&&rows.size()==2&&rows[0].text=="original unresolved"&&rows[0].evidence.empty(),
      "long announced turn cropped");
    for(size_t speech:{8000u,0u}) {
      rows=bound.announced(100000,100000,speech);
      check(bound.separation->calls==1&&rows[0].text=="original unresolved","turn older than the window cropped");
    }
    // A short turn still reaches the qualified floor from earlier capture.
    rows=bound.announced(40000,10000,8000);
    check(bound.separation->calls==2&&bound.separation->input==std::vector<float>(bound.fed.begin()+16000,bound.fed.end())&&
      rows[0].start==16000&&rows[0].end==48000,"short announced turn lost its qualified window");
    // Without an announced onset the whole capture must still fit.
    bound.recognizer.begin();std::vector<float> quiet(512,.2f);
    for(size_t at=0;at<100000;at+=512)bound.recognizer.push(quiet.data(),512);
    bound.recognizer.finish();rows=bound.recognizer.segments();bound.recognizer.reset();
    check(bound.separation->calls==2&&rows[0].text=="original unresolved","unannounced long capture cropped");
    rows=bound.turn();check(bound.separation->calls==3&&rows[0].start==0&&rows[0].end==48000,"announcement leaked into next capture");
    refuses([&]{bound.recognizer.speech_onset(0);},"onset outside a capture admitted");
    bound.recognizer.begin();bound.recognizer.push(quiet.data(),quiet.size());
    refuses([&]{bound.recognizer.speech_onset(513);},"onset after captured input admitted");
    bound.recognizer.speech_onset(0);refuses([&]{bound.recognizer.speech_onset(0);},"second onset admitted");
    bound.recognizer.reset();
  }
  {
    // A separator or source model that fails to run keeps the completed live
    // rows with a typed reason (P1); it neither ends the turn nor publishes a
    // model message. The source recognizer is retired and the owner recovers.
    for(int fault=0;fault<8;++fault) {
      Fixture failing;auto& source=*failing.source;failing.recognizer.open();
      switch(fault) {
        case 0:failing.separation->on_separate=[]{throw std::runtime_error("out of memory at a private path");};break;
        case 1:failing.separation->on_separate=[]{throw std::bad_alloc();};break;
        case 2:failing.separation->alter=[](Waveforms& w){w[1][7]=std::numeric_limits<float>::quiet_NaN();};break;
        case 3:failing.separation->alter=[](Waveforms& w){w[0].pop_back();};break;
        case 4:failing.separation->on_separate=[]{throw aii::multitalker::SourceContractViolation("non-finite output");};break;
        case 5:source.on_open=[&]{if(source.opens==2)throw std::runtime_error("source initialization");};break;
        case 6:source.on_push=[&]{if(source.pcm.size()>20000)throw std::runtime_error("source inference");};break;
        case 7:source.on_finish=[&]{if(source.opens==2)throw std::invalid_argument("source model refusal");};break;
      }
      failing.recognizer.begin();std::vector<float> pcm(48000,.2f);failing.recognizer.push(pcm.data(),pcm.size());
      size_t stages=0;check(failing.recognizer.finish_with_progress([&]{++stages;}).empty(),"pooled finish");
      rows=failing.recognizer.segments();
      check(rows.size()==2&&rows[0].text=="original unresolved"&&rows[1].text=="original unresolved"&&
        rows[0].evidence.empty()&&rows[1].evidence.empty(),"failed separation lost the live transcript");
      for(const auto& row:rows)check(row.evidence_unavailable=="speaker_separation_failed","separation failure not reported");
      check(failing.last()==(fault<5?"separator_failed":"source_failed")&&failing.count(failing.last())==1&&
        failing.recognizer.execution_info().find("private")==std::string::npos,"separation failure cause not reported");
      check(source.resets>0&&source.pcm.empty()&&stages>0,"failed source recognizer was not retired");
      failing.recognizer.reset();
      failing.separation->on_separate={};failing.separation->alter={};source.on_open={};source.on_push={};source.on_finish={};
      rows=failing.turn();
      check(rows.size()==2&&rows[0].text=="first source"&&rows[0].evidence_unavailable.empty(),"separation did not recover");
    }
    // Only competing rows are relabelled; other dispositions stay intact.
    Fixture mixed;mixed.recognizer.open();mixed.live->second_reason="speaker_activity_unavailable";
    mixed.separation->on_separate=[]{throw std::runtime_error("provider failure");};
    rows=mixed.turn();
    check(rows.size()==2&&rows[0].evidence_unavailable=="speaker_separation_failed"&&
      rows[1].evidence_unavailable=="speaker_activity_unavailable","failure relabelled a non-competing row");
    // Cancellation is never a tolerated failure, however the model reports it.
    for(int phase=0;phase<2;++phase) {
      Fixture stop;stop.recognizer.open();
      auto cancel=[&]{stop.recognizer.cancel();throw std::runtime_error("terminated");};
      if(phase==0)stop.separation->on_separate=cancel;else stop.source->on_push=cancel;
      stop.recognizer.begin();std::vector<float> pcm(48000,.2f);stop.recognizer.push(pcm.data(),pcm.size());
      bool cancelled=false;
      try{stop.recognizer.finish();}catch(const aii::voice::Cancelled&){cancelled=true;}catch(const std::exception&){}
      check(cancelled&&stop.last()=="none","cancelled separation was reported as a model failure");
      refuses([&]{stop.recognizer.segments();},"cancelled finals escaped");
    }
    // A progress refusal is the caller's deadline or stop: it propagates
    // unchanged from any source stage, including nested source refinement.
    for(size_t refusal:{2u,10u,53u}) {
      Fixture late;late.source->finish_stages=3;late.recognizer.open();late.recognizer.begin();
      std::vector<float> pcm(48000,.2f);late.recognizer.push(pcm.data(),pcm.size());
      size_t stages=0;std::string reason;
      try{late.recognizer.finish_with_progress([&]{
        if(++stages==refusal)throw std::runtime_error("recognition model call exceeded progress deadline");});}
      catch(const std::exception& e){reason=e.what();}
      check(reason=="recognition model call exceeded progress deadline","progress refusal hidden as a model failure");
      check(late.source->finishes==(refusal>51?1u:0u),"refusal stage not exercised");
      refuses([&]{late.recognizer.segments();},"refused progress published a final");
    }
    // A source result that violates the binding contract is an integrity fault.
    Fixture corrupt;corrupt.source->corrupt=true;corrupt.recognizer.open();
    refuses([&]{corrupt.turn();},"mismatched source evidence tolerated");
    refuses([&]{corrupt.recognizer.segments();},"mismatched source evidence published a final");
  }
  {
    // A separator still running at its budget is cancelled without cancelling
    // the session. The turn keeps its live rows and says why; the separator is
    // re-armed and the next turn separates. The watchdog is never approached.
    using std::chrono::milliseconds;using Clock=std::chrono::steady_clock;
    const milliseconds budget(200);
    Fixture slow({250,budget,budget});slow.recognizer.open();slow.separation->block=true;
    const auto opened=slow.separation->opens;const auto started=Clock::now();
    rows=slow.turn();const auto elapsed=Clock::now()-started;
    check(elapsed>=budget&&elapsed<budget+milliseconds(1000),"separation did not return at its budget");
    check(rows.size()==2&&rows[0].text=="original unresolved"&&rows[0].evidence_unavailable=="speaker_separation_failed"&&
      rows[1].evidence_unavailable=="speaker_separation_failed","expired separation lost the live rows");
    check(slow.last()=="budget_expired"&&slow.count("budget_expired")==1&&slow.count("separator_failed")==0,
      "budget expiry not reported");
    check(slow.separation->cancels==1&&slow.separation->opens==opened+1&&slow.source->begins==0,
      "expired separator not cancelled and re-armed");
    slow.separation->block=false;rows=slow.turn();
    check(rows.size()==2&&rows[0].text=="first source"&&slow.last()=="replaced","re-armed separator did not separate");
    // A result that ignores cancellation and arrives after the budget is
    // abandoned with it; the separator is still re-armed.
    slow.separation->ignore_cancel=true;
    // The separator holds its result until the budget's cancel has reached it, so the result arrives after
    // the budget by construction. A sleep past the budget would leave the timer thread 100 ms to be scheduled,
    // which a loaded hosted runner can overrun; the guard only keeps a broken budget from hanging the test.
    const size_t cancelled_before=slow.separation->cancels;
    slow.separation->on_separate=[&]{
      const auto give_up=Clock::now()+std::chrono::seconds(4);
      while(slow.separation->cancels<=cancelled_before&&Clock::now()<give_up)std::this_thread::sleep_for(milliseconds(1));
    };
    rows=slow.turn();
    check(rows[0].text=="original unresolved"&&slow.last()=="budget_expired"&&slow.count("budget_expired")==2&&
      slow.separation->opens==opened+2&&slow.source->begins==2,"late separator result replaced the live rows");
    // A fast separator under a tight budget still replaces the rows.
    Fixture fast({250,milliseconds(2000),milliseconds(2000)});fast.recognizer.open();
    rows=fast.turn();check(rows[0].text=="first source"&&fast.last()=="replaced","budget refused a fast separator");
    // A session cancel during a budgeted separation is cancellation, at once.
    Fixture stop({250,milliseconds(10000),milliseconds(10000)});stop.recognizer.open();stop.separation->block=true;
    std::mutex mutex;std::condition_variable changed;bool entered=false;
    stop.separation->on_separate=[&]{std::lock_guard<std::mutex> lock(mutex);entered=true;changed.notify_all();};
    stop.recognizer.begin();std::vector<float> pcm(48000,.2f);stop.recognizer.push(pcm.data(),pcm.size());
    bool cancelled=false;Clock::time_point retired;
    std::thread inference([&]{
      try{stop.recognizer.finish();}catch(const aii::voice::Cancelled&){cancelled=true;}catch(...){}
      retired=Clock::now();
    });
    {std::unique_lock<std::mutex> lock(mutex);changed.wait(lock,[&]{return entered;});}
    const auto requested=Clock::now();stop.recognizer.cancel();inference.join();
    check(cancelled&&retired-requested<milliseconds(1000),"session cancel waited for or became the budget");
    check(stop.count("budget_expired")==0&&stop.last()=="none","session cancel reported as a separation outcome");
    refuses([&]{stop.recognizer.segments();},"cancelled separation published finals");
  }
  f.source->pooled=true;refuses([&]{f.turn();},"pooled source accepted");
  refuses([&]{f.recognizer.segments();},"partial failed source result escaped");
  f.recognizer.reset();f.source->pooled=false;check(f.turn().size()==2,"fault recovery failed");
  // Cancel admission cannot wait behind active inference. No late result may escape.
  for(int phase=0;phase<3;++phase) {
    Fixture c;std::mutex mutex;std::condition_variable changed;bool entered=false,release=false;
    auto stall=[&]{std::unique_lock<std::mutex> lock(mutex);entered=true;changed.notify_all();
      changed.wait(lock,[&]{return release;});};
    if(phase==0)c.live->on_finish=stall;
    if(phase==1)c.separation->on_separate=stall;
    if(phase==2)c.source->on_finish=stall;
    c.recognizer.open();c.recognizer.begin();std::vector<float> pcm(48000,.2f);c.recognizer.push(pcm.data(),pcm.size());
    bool cancelled=false;std::exception_ptr error;
    std::thread inference([&]{try{c.recognizer.finish();}catch(const aii::voice::Cancelled&){cancelled=true;}catch(...){error=std::current_exception();}});
    {std::unique_lock<std::mutex> lock(mutex);changed.wait(lock,[&]{return entered;});}
    const auto start=std::chrono::steady_clock::now();c.recognizer.cancel();
    const auto elapsed=std::chrono::steady_clock::now()-start;
    {std::lock_guard<std::mutex> lock(mutex);release=true;changed.notify_all();}inference.join();
    if(error)std::rethrow_exception(error);
    check(elapsed<std::chrono::milliseconds(50)&&cancelled,"cancel waited or stale result escaped");
    check(c.live->cancels&&c.source->cancels&&c.separation->cancels,"cancellation missed component");
    refuses([&]{c.recognizer.segments();},"cancelled finals escaped");
    c.recognizer.reset();c.live->on_finish={};c.separation->on_separate={};c.source->on_finish={};
    c.recognizer.open();check(c.turn().size()==2,"cancel recovery failed");
  }
  std::cout<<"resident separation contracts passed\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
