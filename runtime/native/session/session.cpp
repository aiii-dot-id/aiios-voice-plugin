#include "session.h"
#include "speaker_limits.h"
#include "text.h"
#include "../../native_endpoint/pause_gate.h"
#include <array>
#include <atomic>
#include <cmath>
#include <condition_variable>
#include <deque>
#include <map>
#include <mutex>
#include <stdexcept>
#include <thread>
#ifdef AII_ANDROID_ENDPOINT_HINT
#include "android_endpoint_hint.h"
#endif

namespace aii::voice {
namespace {
constexpr size_t block_size=512, input_bound=32768, output_bound=120000;
struct Block { std::array<float,block_size> pcm{}; size_t valid=block_size; float probability=0; };
struct Job {
  uint64_t id=0, generated=0, delivered=0, rendered=0;
  std::vector<std::string> segments;
  size_t segment_count=0, completed_segments=0;
  bool fenced=false, cancelled=false, retired=false, end_taken=false, receipt=false, stopped=false;
  std::deque<Audio> audio;
  size_t queued=0;
};
struct Query {
  uint64_t id;
  uint64_t admitted_ns=0;
  std::vector<float> pcm;
  std::promise<double> verdict;
};
struct SpeakerJob { Event final; std::vector<float> pcm; };
void require(bool test,const char* reason) { if (!test) throw std::invalid_argument(reason); }
}
struct Session::Impl {
  const std::optional<Hearing> hearing;
  Synthesizer& tts;
  SpeakerIdentifier* speaker;
  const Settings settings;
  const uint64_t input_limit;
  mutable std::mutex mutex;
  std::condition_variable changed;
  std::vector<std::thread> owners;
  std::atomic<bool> stopping{false};
  uint32_t retired=0;
  bool finished=false, control_done=false, input_done=false, closing=false, aborted=false;
  bool cutoff_set=false, recognition_active=false;
  uint64_t cutoff=0;
  std::chrono::steady_clock::time_point tail_deadline;
  size_t backpressured_tail_count=0;
  std::chrono::steady_clock::time_point tail_backpressure_started;
  uint64_t received=0, controlled=0, recognized=0, sequence=0, last_generation=0;
  size_t queued_output=0;
  std::string error;
  const uint64_t endpoint_trace_owner=aii::endpoint::timing::owner();
  std::deque<std::vector<float>> input;
  std::deque<Block> blocks;
  std::deque<Event> events;
  std::deque<std::shared_ptr<Query>> queries;
  std::map<uint64_t,std::shared_ptr<Job>> jobs;
  std::shared_ptr<Job> current;
  std::deque<SpeakerJob> speaker_jobs;
  bool speaker_busy=false;

  bool input_capacity_locked(size_t count) const {
    return received-recognized+count<=input_bound && input.size()<64;
  }
  void resume_tail_clock_locked() {
    if(backpressured_tail_count && input_capacity_locked(backpressured_tail_count)) {
      tail_deadline+=std::chrono::steady_clock::now()-tail_backpressure_started;
      backpressured_tail_count=0;
    }
  }

  Impl(Synthesizer& t,Settings s,std::optional<Hearing> h)
      :hearing(h),tts(t),speaker(h ? h->speaker : nullptr),settings(s),
       input_limit(s.capture_limit_minutes ? capture_samples(s.capture_limit_minutes) : input_clock_max) {
    require(s.pause_ms>=320 && s.pause_ms<=5000,"pause must be 320..5000 ms");
    require(s.input_tail_timeout_ms>=1 && s.input_tail_timeout_ms<=30000,"tail deadline must be 1..30000 ms");
    require(std::isfinite(s.speech_threshold) && s.speech_threshold>0 && s.speech_threshold<1,
            "speech threshold must be finite and inside (0,1)");
    require(s.speech.tts_language=="en" && s.speech.stt_language=="en","this native model profile supports English only");
    require(std::isfinite(s.speech.temperature) && s.speech.temperature>=0 && s.speech.temperature<=1,"temperature must be finite and inside [0,1]");
    require(!s.speech.voice.empty() && s.speech.voice.size()<=64,"bounded preset name required");
    tts.configure(s.speech);
    if(hearing) { hearing->recognizer.open(); hearing->endpoint.open(); }
    tts.open(); if(speaker) speaker->open();
    try {
      if(hearing) {
        launch([this]{control_loop();}); launch([this]{recognition_loop();});
        launch([this]{endpoint_loop();});
      }
      launch([this]{synthesis_loop();});
      if(speaker) launch([this]{speaker_loop();});
    } catch (...) {
      // Published under the lock, as fault() publishes it: a worker between
      // its predicate check and its wait would otherwise miss the wakeup, and
      // the join below would never return.
      { std::lock_guard<std::mutex> lock(mutex); stopping=true; }
      changed.notify_all(); cancel_models();
      for(auto& owner:owners) owner.join();
      throw;
    }
  }
  template<class F> void launch(F f) {
    owners.emplace_back([this,f]{
      try { f(); }
      catch(const Cancelled& e) { if(!stopping) fault(e.what()); }
      catch(const std::exception& e) { fault(e.what()); }
      catch(...) { fault("unknown native session worker failure"); }
      { std::lock_guard<std::mutex> lock(mutex); ++retired; }
      changed.notify_all();
    });
  }
  void emit_locked(Event event) {
    if(events.size()>=1024) throw std::runtime_error("canonical event queue full");
    event.sequence=++sequence; events.push_back(std::move(event));
  }
  void emit(Event event) {
    std::lock_guard<std::mutex> lock(mutex);
    if(!stopping) emit_locked(std::move(event));
  }
  void cancel_models() noexcept {
    if(hearing) { hearing->recognizer.cancel(); hearing->endpoint.cancel(); }
    if(speaker) speaker->cancel();
    uint64_t id=0;
    { std::lock_guard<std::mutex> lock(mutex); if(current) id=current->id; }
    if(id) tts.cancel(id);
  }
  void fault(const std::string& reason) noexcept {
    {
      std::lock_guard<std::mutex> lock(mutex);
      if(error.empty()) error=reason;
      stopping=true;
      for(auto& item:jobs) { item.second->fenced=true; item.second->audio.clear(); item.second->queued=0; }
      queued_output=0;
    }
    changed.notify_all(); cancel_models();
  }
  void maybe_close_locked() {
    if(!closing || (hearing && !input_done) || speaker_busy) return;
    for(const auto& item:jobs) {
      const auto& j=*item.second;
      if(!j.retired || !j.end_taken || !j.receipt) return;
    }
    stopping=true; changed.notify_all();
  }
  void interrupt(uint64_t id,uint64_t position=0,bool cancel_synthesis=true) {
    bool cancel=false;
    {
      std::lock_guard<std::mutex> lock(mutex);
      const auto it=jobs.find(id);
      if(it==jobs.end()&&position)return; // VAD selected a since-settled job
      require(it!=jobs.end(),"unknown synthesis generation");
      auto& j=*it->second;
      // A render receipt never prevents cancelling still-running compute.
      if(!j.fenced && !j.receipt) {
        j.fenced=true;
        queued_output-=j.queued; j.audio.clear(); j.queued=0;
        if(j.retired && !j.end_taken) j.audio.push_back(Audio{id,j.delivered,true,{}});
        emit_locked(Event{0,0,id,position,position,"interruption_requested",cancel_synthesis?"cancel_synthesis":"stop_playback"});
      }
      if(cancel_synthesis && !j.retired && !j.cancelled) { j.cancelled=true; cancel=true; }
    }
    changed.notify_all();
    if(cancel) tts.cancel(id); // never under the state/queue mutex
  }
  void control_loop() {
    auto& vad=hearing->vad;
    vad.reset();
    std::vector<float> tail;
    auto consume=[&](Block block) {
      if(stopping) return;
      block.probability=vad.score(block.pcm.data());
      require(std::isfinite(block.probability) && block.probability>=0 && block.probability<=1,
              "invalid VAD probability");
      std::vector<uint64_t> generations;
      uint64_t position=0;
      {
        std::lock_guard<std::mutex> lock(mutex);
        position=controlled+block.valid;
        for(const auto& item:jobs)
          if((!item.second->receipt && !item.second->fenced) || (!item.second->retired && !item.second->cancelled))
            generations.push_back(item.first);
      }
      if(block.probability>=settings.speech_threshold)
        for(const auto id:generations) interrupt(id,position);
      {
        std::lock_guard<std::mutex> lock(mutex);
        if(stopping) return;
        controlled=position;
        blocks.push_back(std::move(block));
      }
      changed.notify_all();
    };
    for(;;) {
      std::vector<float> packet;
      {
        std::unique_lock<std::mutex> lock(mutex);
        while(!stopping && input.empty() && !finished) {
          resume_tail_clock_locked();
          if(cutoff_set && !backpressured_tail_count) {
            if(changed.wait_until(lock,tail_deadline)==std::cv_status::timeout && !finished && input.empty() && !backpressured_tail_count)
              throw std::runtime_error("input tail missing at admitted cutoff");
          } else changed.wait(lock);
        }
        resume_tail_clock_locked();
        if(cutoff_set && !finished && !backpressured_tail_count && std::chrono::steady_clock::now()>=tail_deadline)
          throw std::runtime_error("input tail missing at admitted cutoff");
        if(stopping) return;
        if(input.empty()) break;
        packet=std::move(input.front()); input.pop_front();
      }
      tail.insert(tail.end(),packet.begin(),packet.end());
      size_t offset=0;
      for(;offset+block_size<=tail.size();offset+=block_size) {
        Block block; std::copy_n(tail.data()+offset,block_size,block.pcm.data()); consume(block);
      }
      tail.erase(tail.begin(),tail.begin()+offset);
    }
    if(!tail.empty()) {
      Block block; block.valid=tail.size(); std::copy(tail.begin(),tail.end(),block.pcm.begin()); consume(block);
    }
    { std::lock_guard<std::mutex> lock(mutex); control_done=true; }
    changed.notify_all();
  }
  std::shared_future<double> query(uint64_t id,std::vector<float> pcm) {
    auto q=std::make_shared<Query>(); q->id=id; q->pcm=std::move(pcm);
    auto future=q->verdict.get_future().share();
    {
      std::lock_guard<std::mutex> lock(mutex);
      if(stopping || queries.size()>=4) throw std::runtime_error("endpoint admission unavailable");
      queries.push_back(q);
      q->admitted_ns=aii::endpoint::timing::now();
    }
    aii::endpoint::timing::record(endpoint_trace_owner,id,"queued",q->admitted_ns,0,q->pcm.size());
    changed.notify_all(); return future;
  }
  void endpoint_loop() {
#ifdef AII_ANDROID_ENDPOINT_HINT
    using HintClock=std::chrono::steady_clock;
    const auto target=std::chrono::duration_cast<std::chrono::nanoseconds>(aii::endpoint::PauseGate::timeout).count();
    AndroidEndpointHint backend;
    EndpointWorkHint<AndroidEndpointHint> hint(backend,gettid(),target);
    emit(Event{0,0,0,0,0,"endpoint_scheduling",std::string("{\"api\":\"android_performance_hint\",\"target_ns\":")+
      std::to_string(target)+",\"preferred_rate_ns\":"+std::to_string(backend.preferred_rate_ns)+"}"});
#endif
    for(;;) {
      std::shared_ptr<Query> q;
      {
        std::unique_lock<std::mutex> lock(mutex);
        changed.wait(lock,[&]{return stopping || !queries.empty();});
        if(stopping) {
          for(auto& pending:queries) pending->verdict.set_exception(
              std::make_exception_ptr(std::runtime_error("endpoint retired")));
          queries.clear(); return;
        }
        q=queries.front(); queries.pop_front();
      }
      try {
        aii::endpoint::timing::record(endpoint_trace_owner,q->id,"score_started",q->admitted_ns,0,q->pcm.size());
        const auto score_started_ns=aii::endpoint::timing::now();
#ifdef AII_ANDROID_ENDPOINT_HINT
        const auto started=HintClock::now();
        const auto score=hearing->endpoint.score(q->id,q->pcm);
        const auto work=std::chrono::duration_cast<std::chrono::nanoseconds>(HintClock::now()-started).count();
        hint.report(work);
        aii::endpoint::timing::record(endpoint_trace_owner,q->id,"score_finished",score_started_ns,0,q->pcm.size());
        emit(Event{0,0,0,q->id,q->pcm.size(),"endpoint_work",std::string("{\"actual_ns\":")+
          std::to_string(work)+",\"report_accepted\":true}"});
        q->verdict.set_value(score);
#else
        const auto score=hearing->endpoint.score(q->id,q->pcm);
        aii::endpoint::timing::record(endpoint_trace_owner,q->id,"score_finished",score_started_ns,0,q->pcm.size());
        q->verdict.set_value(score);
#endif
      }
      catch(...) {
        aii::endpoint::timing::record(endpoint_trace_owner,q->id,"score_failed",q->admitted_ns,0,q->pcm.size());
        q->verdict.set_exception(std::current_exception()); throw;
      }
    }
  }
  void recognition_loop() {
    auto& asr=hearing->recognizer;
    using Gate=aii::endpoint::PauseGate;
    Gate gate([this](uint64_t id,std::vector<float> p){return query(id,std::move(p));},
              [this](const Gate::Event& e){
      emit(Event{0,0,0,e.query_position,e.resolution_position,
                 e.kind==Gate::Event::Kind::Query?"pause_query":"pause_resolved",std::to_string(e.probability)});
    },endpoint_trace_owner);
    gate.configure_pause(settings.pause_ms);
    std::deque<Block> preroll, provisional;
    uint64_t position=0,valid_position=0,silence=0,turn=0,start=0;
    bool active=false, boundary=false;
    const bool continuous=asr.continuous_input();
    bool begun=false;
    uint64_t consumed=0;
    std::vector<float> speaker_pcm;
    bool speaker_overflow=false;
    auto retain_speaker=[&](const Block& b) {
      if(!speaker || asr.separated() || speaker_overflow) return;
      if(speaker_pcm.size()+b.valid>480000) {
        speaker_pcm.clear(); speaker_overflow=true; return;
      }
      speaker_pcm.insert(speaker_pcm.end(),b.pcm.begin(),b.pcm.begin()+b.valid);
    };
    std::string last;
    auto partial=[&](std::string text) {
      if(asr.separated()) {
        if(!text.empty())throw std::runtime_error("separated recognizer returned pooled text");
        return;
      }
      if(!text.empty() && text!=last && !stopping) {
        last=std::move(text); emit(Event{0,turn,0,start,valid_position,"transcript_partial",last});
      }
    };
    auto push=[&](const Block& b){
      if(continuous && !begun){asr.begin();begun=true;start=consumed;}
      partial(asr.push(b.pcm.data(),b.valid));
      if(continuous)consumed+=b.valid;
    };
    auto complete=[&](const char* reason) {
      if(!active) return;
      const auto text=asr.finish(); partial(text);
      const bool separated=asr.separated();
      const auto segments=separated?asr.segments():std::vector<RecognizedSegment>{};
      if(segments.size()>128)throw std::runtime_error("recognizer segment bound");
      for(const auto& segment:segments) {
        if(segment.track.empty() || segment.track.size()>63 || segment.text.empty() || segment.text.size()>131071 ||
           segment.start>=segment.end || segment.end>valid_position-start)
          throw std::runtime_error("separated recognizer segment extent");
        for(unsigned char c:segment.track)if(!((c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||c=='-'||c=='_'||c=='.'))
          throw std::runtime_error("recognizer track identifier");
        if(!segment.evidence.empty()) {
          if(segment.evidence.size()<31920 || segment.evidence.size()>160000 ||
             segment.evidence_start<segment.start || segment.evidence_start>segment.end ||
             segment.evidence.size()>segment.end-segment.evidence_start)
            throw std::runtime_error("recognizer track evidence extent");
          for(float x:segment.evidence)if(!std::isfinite(x)||x<-1||x>1)
            throw std::runtime_error("recognizer track evidence PCM");
        }
      }
      asr.reset(); active=false; begun=false;
      { std::lock_guard<std::mutex> lock(mutex); recognition_active=false; }
      if(separated) {
        std::lock_guard<std::mutex> lock(mutex);
        if(!stopping)for(const auto& segment:segments) {
          Event final{0,turn,0,start+segment.start,start+segment.end,"transcript_final",segment.text};
          final.track=segment.track;emit_locked(final);final.sequence=sequence;
          // Only selected track evidence reaches matching. An empty sample
          // asks for an anonymous provisional bucket, never pooled inference.
          if(speaker && speaker_jobs.size()<speaker_queue_capacity) {
            speaker_jobs.push_back({final,segment.evidence});
            speaker_busy=true;changed.notify_all();continue;
          }
          Event observation{0,turn,0,final.start,final.end,"speaker_observation",
              R"({"outcome":"unavailable","reason":"speaker_worker_unavailable","used_for_permissions":false})",final.sequence};
          observation.track=segment.track;emit_locked(std::move(observation));
        }
      }
      if(!text.empty() || !last.empty()) {
        std::lock_guard<std::mutex> lock(mutex);
        if(!stopping) {
          Event final{0,turn,0,start,valid_position,"transcript_final",text};
          emit_locked(final); final.sequence=sequence;
          if(speaker) {
            const char* unavailable=nullptr;
            if(speaker_overflow) unavailable="utterance_exceeds_uid_context";
            else if(speaker_pcm.size()!=valid_position-start) unavailable="uid_audio_span_unavailable";
            else if(speaker_pcm.size()<31920) unavailable="utterance_too_short";
            else if(speaker_busy) unavailable="speaker_worker_busy";
            if(unavailable) {
              emit_locked(Event{0,turn,0,start,valid_position,"speaker_observation",
                std::string("{\"outcome\":\"unavailable\",\"reason\":\"")+unavailable+"\",\"used_for_permissions\":false}",final.sequence});
            } else {
              speaker_jobs.push_back({final,std::move(speaker_pcm)});
              speaker_busy=true; changed.notify_all();
            }
          }
        }
      }
      if(!text.empty() || !segments.empty()) emit(Event{0,turn,0,start,valid_position,"turn_committed",reason});
      preroll.clear(); last.clear(); silence=0; gate.reset();
      speaker_pcm.clear(); speaker_overflow=false;
    };
    try {
      for(;;) {
        Block block;
        {
          std::unique_lock<std::mutex> lock(mutex);
          changed.wait(lock,[&]{return stopping || !blocks.empty() || control_done;});
          if(stopping) break;
          if(blocks.empty()) {
            lock.unlock();
            for(const auto& b:provisional) push(b);
            const char* reason=settings.capture_limit_minutes && cutoff==input_limit ? "capture_limit" : "finish_input";
            provisional.clear(); complete(reason); gate.close();
            lock.lock(); input_done=true;
            emit_locked(Event{0,0,0,received,recognized,"input_finished",reason});
            maybe_close_locked(); changed.notify_all(); break;
          }
          block=std::move(blocks.front()); blocks.pop_front();
        }
        position+=block_size; valid_position+=block.valid;
        const bool speech=block.probability>=settings.speech_threshold;
        preroll.push_back(block); if(preroll.size()>32) preroll.pop_front();
        if(!active && speech) {
          active=true; ++turn; silence=0; last.clear(); boundary=false;
          { std::lock_guard<std::mutex> lock(mutex); recognition_active=true; }
          uint64_t retained=0; for(const auto& b:preroll) retained+=b.valid;
          if(!continuous)start=valid_position-retained;
          else if(!begun)start=consumed;
          speaker_pcm.clear(); speaker_overflow=false;
          if(!continuous)asr.begin();
          gate.reset();
          emit(Event{0,turn,0,start,valid_position,"speech_start",""});
          for(const auto& b:preroll) {
            retain_speaker(b); gate.append(b.pcm.data(),block_size);
            if(!continuous)push(b);
          }
          if(continuous)push(block);
        } else if(active) {
          retain_speaker(block);
          gate.append(block.pcm.data(),block_size);
          if(boundary) provisional.push_back(block); else push(block);
        } else if(continuous) {
          // Preserve the actual capture and feature-clock origin. Discarding
          // pre-VAD context changed diarization and lost the quieter speaker.
          push(block);
        }
        if(active && !stopping) {
          silence=speech?0:silence+block_size;
          const auto decision=gate.poll(speech,silence,position);
          if(gate.pending() && !boundary) boundary=true;
          if(boundary && (speech || (!gate.pending() && decision==Gate::Decision::None))) {
            for(const auto& b:provisional) push(b);
            provisional.clear(); boundary=false;
          }
          if(provisional.size()>4) throw std::runtime_error("endpoint provisional tail exceeded bound");
          if(decision!=Gate::Decision::None) {
            complete(decision==Gate::Decision::SemanticNoHold?"semantic_pause":"bounded_silence");
            if(continuous)for(const auto& b:provisional)push(b);
            preroll=std::move(provisional); provisional.clear(); boundary=false;
          }
        }
        { std::lock_guard<std::mutex> lock(mutex); recognized=valid_position; }
        changed.notify_all();
      }
      asr.reset();
    } catch(...) { asr.reset(); throw; }
  }
  void speaker_loop() {
    for(;;) {
      SpeakerJob job;
      {
        std::unique_lock<std::mutex> lock(mutex);
        changed.wait(lock,[&]{return stopping || !speaker_jobs.empty();});
        if(stopping) { speaker_jobs.clear(); speaker_busy=false; return; }
        job=std::move(speaker_jobs.front());speaker_jobs.pop_front();
      }
      std::string result;
      try {
        result=job.final.track.empty()?speaker->identify(job.final.sequence,job.pcm):
          speaker->identify_track(job.final.sequence,job.pcm);
        if(result.empty() || result.size()>8192) throw std::runtime_error("invalid speaker observation size");
      } catch(const EnrollmentUnavailable&) {
        result="{\"outcome\":\"unavailable\",\"reason\":\"enrollment_unavailable\",\"used_for_permissions\":false}";
      } catch(const std::exception&) {
        result="{\"outcome\":\"unavailable\",\"reason\":\"speaker_identification_failed\",\"used_for_permissions\":false}";
      }
      {
        std::lock_guard<std::mutex> lock(mutex);
        if(!stopping) emit_locked(Event{0,job.final.turn,0,job.final.start,job.final.end,
                                       "speaker_observation",std::move(result),job.final.sequence,job.final.track});
        speaker_busy=!speaker_jobs.empty(); maybe_close_locked();
      }
      changed.notify_all();
    }
  }
  void synthesis_loop() {
    for(;;) {
      std::shared_ptr<Job> job;
      {
        std::unique_lock<std::mutex> lock(mutex);
        changed.wait(lock,[&]{return stopping || (current && !current->retired);});
        if(stopping) return;
        job=current;
      }
      try {
        for(const auto& part:job->segments) {
          { std::lock_guard<std::mutex> lock(mutex); if(stopping || job->cancelled) break; }
          const auto text=strip_text(part); if(text.empty())continue;
          tts.start(job->id,text);
          uint64_t segment_samples=0; bool natural=false;
          for(;;) {
            { std::lock_guard<std::mutex> lock(mutex); if(stopping || job->cancelled) break; }
            auto pcm=tts.next();
            if(pcm.empty()) {
              if(!segment_samples)throw std::runtime_error("empty synthesis segment");
              natural=true;break;
            }
            require(pcm.size()<=output_bound,"synthesis chunk exceeds bounded queue");
            for(float value:pcm) require(std::isfinite(value),"non-finite synthesis output");
            segment_samples+=pcm.size();
            if(segment_samples>24000*60)throw std::runtime_error("synthesis segment exceeded 60 seconds");
            std::unique_lock<std::mutex> lock(mutex);
            job->generated+=pcm.size();
            if(job->generated>24000ULL*30*60)throw std::runtime_error("synthesis reply exceeded 30 minutes");
            if(stopping || job->cancelled)break;
            if(job->fenced)continue; // playback stopped; compute remains independently owned
            if(!changed.wait_for(lock,std::chrono::seconds(15),[&]{return stopping || job->fenced || queued_output+pcm.size()<=output_bound;}))
              throw std::runtime_error("audio consumer did not release bounded output within 15 seconds");
            if(stopping || job->cancelled) break;
            if(job->fenced)continue;
            job->audio.push_back(Audio{job->id,job->generated-pcm.size(),false,std::move(pcm)});
            job->queued+=job->audio.back().pcm.size();queued_output+=job->audio.back().pcm.size();
            changed.notify_all();
          }
          tts.reset();
          { std::lock_guard<std::mutex> lock(mutex); if(natural)++job->completed_segments; }
        }
      } catch(const Cancelled&) {
        tts.reset();
        // A fence permits cancellation, never an unrelated inference failure.
        std::lock_guard<std::mutex> lock(mutex);
        if(!job->cancelled && !stopping) throw;
      } catch(...) {
        tts.reset(); throw;
      }
      {
        std::lock_guard<std::mutex> lock(mutex);
        job->retired=true;
        job->segments.clear();
        if(!stopping) {
          const auto end=job->fenced?job->delivered:job->generated;
          job->audio.push_back(Audio{job->id,end,true,{}});
          emit_locked(Event{0,0,job->id,0,end,job->cancelled?"synthesis_cancelled":"synthesis_end",""});
          maybe_close_locked();
        }
      }
      changed.notify_all();
    }
  }
};
Session::Session(Synthesizer& t,Settings s,std::optional<Hearing> h)
    :p_(std::make_unique<Impl>(t,s,h)) {}
Session::Session(Recognizer& a,Vad& v,Endpoint& e,Synthesizer& t,Settings s,SpeakerIdentifier* u)
    :Session(t,s,Hearing{a,v,e,u}) {}
Session::~Session() {
  close(true);
  // Never free a model under an executing kernel. The embedding process owns
  // the outer kill deadline if a backend does not retire after cancellation.
  for(auto& owner:p_->owners) if(owner.joinable()) owner.join();
}
bool Session::feed(uint64_t start,const float* data,size_t count) {
  require(p_->hearing.has_value(),"session has no input direction");
  require(data && count && count<=input_bound,"bounded nonempty input required");
  for(size_t i=0;i<count;++i) require(std::isfinite(data[i]),"input is not finite");
  std::lock_guard<std::mutex> lock(p_->mutex);
  require(!p_->finished && !p_->stopping && (!p_->closing || p_->cutoff_set),"input admission closed");
  require(start==p_->received && p_->received<=p_->input_limit && count<=p_->input_limit-p_->received,"input clock/bound differs");
  require(!p_->cutoff_set || count<=p_->cutoff-p_->received,"audio exceeds admitted cutoff");
  p_->resume_tail_clock_locked();
  require(!p_->cutoff_set || p_->backpressured_tail_count || std::chrono::steady_clock::now()<p_->tail_deadline,"input tail arrived after deadline");
  if(!p_->input_capacity_locked(count)) {
    // The caller has supplied the next valid PCM, but our bounded recognizer
    // queue cannot take it. This is computation backpressure, not missing
    // transport data. Suspend only that wait; status and repeated offers do
    // not renew it. Recognition progress resumes the remaining budget even
    // if the caller never retries. Abort still bypasses recognition.
    if(p_->cutoff_set && !p_->backpressured_tail_count) {
      p_->backpressured_tail_count=count;
      p_->tail_backpressure_started=std::chrono::steady_clock::now();
      p_->changed.notify_all();
    }
    return false;
  }
  if(p_->backpressured_tail_count) {
    // A caller may retry its packet in smaller pieces once capacity returns.
    p_->tail_deadline+=std::chrono::steady_clock::now()-p_->tail_backpressure_started;
    p_->backpressured_tail_count=0;
  }
  p_->input.emplace_back(data,data+count); p_->received+=count;
  if(p_->settings.capture_limit_minutes && p_->received==p_->input_limit && !p_->cutoff_set) {
    p_->cutoff_set=true; p_->cutoff=p_->input_limit;
  }
  if(p_->cutoff_set && p_->received==p_->cutoff)p_->finished=true;
  p_->changed.notify_all(); return true;
}
void Session::finish_input(uint64_t end) {
  require(p_->hearing.has_value(),"session has no input direction");
  std::lock_guard<std::mutex> lock(p_->mutex);
  require(!p_->stopping && end>=p_->received && end<=p_->input_limit,"finish requires bounded future input cutoff");
  require(!p_->cutoff_set || end==p_->cutoff,"admitted input cutoff cannot change");
  if(!p_->cutoff_set) {
    p_->cutoff_set=true;p_->cutoff=end;
    p_->tail_deadline=std::chrono::steady_clock::now()+std::chrono::milliseconds(p_->settings.input_tail_timeout_ms);
  }
  p_->finished=p_->received==end; p_->changed.notify_all();
}
void Session::synthesize(uint64_t generation,const std::string& text) {
  auto parts=split_text(text);
  std::lock_guard<std::mutex> lock(p_->mutex);
  require(!p_->stopping && !p_->closing,"synthesis admission closed");
  require(generation>p_->last_generation && p_->jobs.size()<64,"new generation and unresolved-work capacity required");
  require(!p_->current || p_->current->retired,"prior synthesis has not retired");
  auto j=std::make_shared<Job>(); j->id=generation;j->segments=std::move(parts);
  for(const auto& part:j->segments)j->segment_count+=!strip_text(part).empty();
  p_->emit_locked(Event{0,0,generation,0,0,"synthesis_start",""});
  p_->jobs.emplace(generation,j); p_->current=j; p_->last_generation=generation;
  p_->changed.notify_all();
}
void Session::interrupt(uint64_t generation) { p_->interrupt(generation); }
void Session::stop_playback(uint64_t generation) { p_->interrupt(generation,0,false); }
void Session::cancel_synthesis(uint64_t generation) { p_->interrupt(generation,0,true); }
void Session::release_generation(uint64_t generation) {
  std::lock_guard<std::mutex> lock(p_->mutex);
  const auto it=p_->jobs.find(generation);require(it!=p_->jobs.end(),"unknown synthesis generation");
  const auto& j=*it->second;
  require(j.retired&&j.end_taken&&j.receipt&&j.audio.empty()&&j.queued==0,"generation still owns compute/audio/receipt custody");
  require(std::none_of(p_->events.begin(),p_->events.end(),[&](const Event& e){return e.generation==generation;}),"generation events not consumed");
  p_->jobs.erase(it);
}
bool Session::event(Event& event) {
  size_t required=0;return event_bounded(event,SIZE_MAX,required);
}
bool Session::event_bounded(Event& event,size_t capacity,size_t& required) {
  std::lock_guard<std::mutex> lock(p_->mutex);
  required=0;
  if(p_->events.empty()) return false;
  required=p_->events.front().text.size()+1;
  if(capacity<required)return false;
  event=std::move(p_->events.front()); p_->events.pop_front(); return true;
}
bool Session::audio(Audio& audio) {
  size_t required=0;return audio_bounded(audio,SIZE_MAX,required);
}
bool Session::audio_bounded(Audio& audio,size_t capacity,size_t& required) {
  std::lock_guard<std::mutex> lock(p_->mutex);
  required=0;
  if(p_->stopping) return false;
  for(auto& item:p_->jobs) {
    auto& j=*item.second;
    if(j.audio.empty()) continue;
    required=j.audio.front().pcm.size();
    if(capacity<required)return false;
    audio=std::move(j.audio.front()); j.audio.pop_front();
    if(audio.end) { j.end_taken=true; p_->maybe_close_locked(); }
    else { j.queued-=audio.pcm.size(); p_->queued_output-=audio.pcm.size(); j.delivered+=audio.pcm.size(); }
    p_->changed.notify_all(); return true;
  }
  return false;
}
void Session::playback(uint64_t generation,uint64_t rendered,bool terminal,bool stopped) {
  std::lock_guard<std::mutex> lock(p_->mutex);
  const auto it=p_->jobs.find(generation); require(it!=p_->jobs.end(),"unknown playback generation");
  auto& j=*it->second;
  require(rendered>=j.rendered && rendered<=j.delivered,"playback clock contradiction");
  require(!stopped || terminal,"stopped requires terminal evidence");
  require(!terminal || (stopped ? j.fenced : (j.end_taken && j.retired)),"terminal playback precedes fence/END/retirement");
  require(!terminal || stopped || (!j.fenced && rendered==j.generated),"incomplete or fenced audio cannot drain");
  if(j.receipt) require(terminal && stopped==j.stopped && rendered==j.rendered,"terminal receipt changed");
  j.rendered=rendered;
  if(terminal) { j.receipt=true; j.stopped=stopped; }
  p_->maybe_close_locked();
}
void Session::close(bool abort) {
  {
    std::lock_guard<std::mutex> lock(p_->mutex);
    if(p_->stopping) return;
    if(!abort) require(!p_->hearing || p_->cutoff_set,"drain close requires admitted input cutoff");
    p_->closing=true;
    if(abort) {
      p_->aborted=true; p_->stopping=true;
      for(auto& item:p_->jobs) { item.second->fenced=true; item.second->audio.clear(); item.second->queued=0; }
      p_->queued_output=0;
    } else p_->maybe_close_locked();
  }
  p_->changed.notify_all(); if(abort) p_->cancel_models();
}
bool Session::wait_closed(uint32_t milliseconds) {
  std::unique_lock<std::mutex> lock(p_->mutex);
  return p_->changed.wait_for(lock,std::chrono::milliseconds(milliseconds),[&]{return p_->retired==p_->owners.size();});
}
Snapshot Session::status() const {
  std::lock_guard<std::mutex> lock(p_->mutex);
  Snapshot s;
  s.input_enabled=p_->hearing.has_value();
  s.received=p_->received; s.controlled=p_->controlled; s.recognized=p_->recognized;
  s.input_finished=p_->input_done; s.stopping=p_->stopping; s.retired=p_->retired==p_->owners.size();
  s.aborted=p_->aborted; s.error=p_->error;
  s.sequence=p_->sequence;s.cutoff=p_->cutoff;s.cutoff_set=p_->cutoff_set;s.closing=p_->closing;
  s.recognition_active=p_->recognition_active && !s.retired;
  s.draining=p_->speaker_busy; // unresolved identity evidence is not idle
  for(const auto& item:p_->jobs) {
    const auto& j=*item.second;
    s.queued_audio_samples+=j.queued; s.synthesizing|=!j.retired;
    s.draining|=!j.receipt;
  }
  if(p_->current) {s.synthesis_segments=p_->current->segment_count;s.completed_segments=p_->current->completed_segments;}
  s.generation=p_->last_generation; return s;
}
GenerationSnapshot Session::generation(uint64_t id) const {
  std::lock_guard<std::mutex> lock(p_->mutex);
  const auto i=p_->jobs.find(id);require(i!=p_->jobs.end(),"unknown synthesis generation");
  const auto& j=*i->second;
  return {j.generated,j.delivered,j.rendered,j.queued,j.fenced,j.cancelled,j.retired,j.end_taken,j.receipt,j.stopped};
}
}
