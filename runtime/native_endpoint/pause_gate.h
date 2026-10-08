#pragma once
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <deque>
#include <functional>
#include <future>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
#include "timing_trace.h"

namespace aii::endpoint {
// Only the ordered model-audio consumer calls this class. The injected
// executor owns inference on another thread; it must return a promise-backed
// future promptly. Capture/playback interruption never call this barrier.
class PauseGate {
 public:
  using Clock=std::chrono::steady_clock;
  using Submit=std::function<std::shared_future<double>(uint64_t,std::vector<float>)>;
  struct Event {
    // Late: the verdict of the query submitted for a commit point did not
    // come inside the decision wait, and the turn goes on to end by silence.
    enum class Kind { Query, Resolution, Late } kind;
    uint64_t query_id, query_position, resolution_position;
    size_t samples;
    double probability;
    bool stale;
  };
  using Record=std::function<void(const Event&)>;
  enum class Decision { None, SemanticNoHold, BoundedSilence };
  static constexpr uint64_t trigger_samples=10240, commitment_samples=12288, maximum_silence_samples=30720;
  static constexpr double hold_threshold=.01;
  // A latency target told to a scheduler and written in a trace. Nothing
  // waits by it and nothing ends at it.
  static constexpr auto timeout=std::chrono::milliseconds(250);
  // The gate's two waits on the executor's thread, for a caller that states
  // none, a test or a probe: its owner states them (configure_waits; the
  // session states its settings', which a worker takes from its limits table).
  static constexpr auto decision_timeout=std::chrono::seconds(1);
  static constexpr auto retirement_timeout=std::chrono::seconds(15);
  // A query still owned at the input's end did not come back inside the
  // retirement wait. Its owner says which limit that was.
  struct Unretired : std::runtime_error {
    explicit Unretired(std::chrono::milliseconds limit)
        : std::runtime_error("semantic endpoint did not retire in "+std::to_string(limit.count())+" ms"),waited(limit) {}
    std::chrono::milliseconds waited;
  };

  PauseGate(Submit submit, Record record, uint64_t trace_owner=0)
      : submit_(std::move(submit)),record_(std::move(record)),trace_owner_(trace_owner) {
    if (!submit_ || !record_) throw std::invalid_argument("pause executor/recorder required");
  }
  void configure_pause(uint32_t milliseconds) {
    if (position_ || !audio_.empty() || pending_ || !retired_.empty())
      throw std::invalid_argument("pause settings are pinned before session input");
    if (milliseconds<320 || milliseconds>5000)
      throw std::invalid_argument("pause must be 320 through 5000 whole milliseconds");
    commit_ = ((uint64_t(milliseconds)*16+511)/512)*512;
    trigger_ = commit_-2048;
    maximum_ = commit_+18432;
  }
  // decision: at a turn's commit point, how long the verdict of the query
  // submitted for it is waited for, counted from its submission. Past it the
  // turn ends by silence alone and the verdict, when it comes, is stale.
  // retirement: at the input's end, how long each query still owned is
  // waited for; past it close() refuses with Unretired.
  void configure_waits(std::chrono::milliseconds decision,std::chrono::milliseconds retirement) {
    if (position_ || !audio_.empty() || pending_ || !retired_.empty())
      throw std::invalid_argument("pause waits are pinned before session input");
    if (decision.count()<=0 || retirement.count()<=0)
      throw std::invalid_argument("pause waits must be stated");
    decision_=decision; retirement_=retirement;
  }
  uint64_t commitment() const { return commit_; }
  bool pending() const { return bool(pending_); }
  void append(const float* data,size_t count) {
    if (!data || !count || count%512 || count>960000) throw std::invalid_argument("pause requires bounded 512-sample blocks");
    for (size_t i=0;i<count;++i) if (!std::isfinite(data[i])) throw std::invalid_argument("pause PCM not finite");
    for (size_t i=0;i<count;i+=512) {
      std::array<float,512> block;
      std::copy(data+i,data+i+512,block.begin());
      audio_.push_back(block);
      if (audio_.size()>250) audio_.pop_front();
    }
  }
  // Reset preserves unresolved inference ownership; errors cannot disappear
  // merely because speech resumed or a new utterance began.
  void reset() { retire(); audio_.clear(); checked_=false; }
  Decision poll(bool speech,uint64_t silence,uint64_t position) {
    if (position<position_ || position%512 || silence%512 || silence>position)
      throw std::invalid_argument("pause source clock differs");
    position_=position;
    if (speech) { retire(); checked_=false; }
    reap(false);
    if (speech) return Decision::None;
    if (silence>=maximum_) return Decision::BoundedSilence;
    if (silence>=trigger_ && !checked_ && !pending_ && retired_.empty()) {
      if (audio_.empty()) throw std::runtime_error("cannot classify empty turn");
      if (next_id_==std::numeric_limits<uint64_t>::max()) throw std::overflow_error("pause query ids exhausted");
      std::vector<float> samples; samples.reserve(audio_.size()*512);
      for (const auto& block:audio_) samples.insert(samples.end(),block.begin(),block.end());
      const auto id=++next_id_;
      const auto count=samples.size();
      record_({Event::Kind::Query,id,position,position,count,0,false});
      timing::record(trace_owner_,id,"submit",0,0,count);
      // Store the returned future before validation so an invalid executor
      // verdict cannot silently look like an absent query.
      auto future=submit_(id,std::move(samples));
      if (!future.valid()) throw std::runtime_error("pause executor returned no ownership");
      pending_=std::make_unique<Query>(Query{future,id,position,count,Clock::now()});
      trace(*pending_,"armed");
    }
    if (pending_ && silence>=commit_) {
      // Hold this audio-clock boundary briefly for the exact model verdict.
      // If inference exceeds its bounded decision budget, keep its ownership
      // as stale evidence and fall back to the acoustic maximum. Neither a
      // busy model nor transport batch timing may fault or move this turn.
      if (pending_->future.wait_until(pending_->started+decision_)==std::future_status::ready) {
        const auto probability=resolve(*pending_,false,position);
        pending_.reset(); checked_=true;
        if (probability>hold_threshold) return Decision::SemanticNoHold;
      } else {
        trace(*pending_,"late_acoustic");
        record_({Event::Kind::Late,pending_->id,pending_->position,position,pending_->samples,0,true});
        retire();checked_=true;
      }
    }
    return Decision::None;
  }
  void close() { retire(); reap(true); }
  size_t outstanding() const { return retired_.size()+(pending_?1:0); }
  size_t samples() const { return audio_.size()*512; }
 private:
  struct Query { std::shared_future<double> future; uint64_t id,position; size_t samples; Clock::time_point started; };
  struct Retired { std::unique_ptr<Query> query; uint64_t position; };
  Submit submit_; Record record_;
  std::deque<std::array<float,512>> audio_;
  std::unique_ptr<Query> pending_;
  std::deque<Retired> retired_;
  uint64_t position_=0,next_id_=0;
  uint64_t trigger_=trigger_samples,commit_=commitment_samples,maximum_=maximum_silence_samples;
  bool checked_=false;
  std::chrono::milliseconds decision_=decision_timeout,retirement_=retirement_timeout;
  uint64_t trace_owner_;
  void trace(const Query& query,const char* phase) const noexcept {
#ifdef AII_ENDPOINT_TIMING_TRACE
    const auto start=std::chrono::duration_cast<std::chrono::nanoseconds>(query.started.time_since_epoch()).count();
    const auto due=std::chrono::duration_cast<std::chrono::nanoseconds>((query.started+timeout).time_since_epoch()).count();
    timing::record(trace_owner_,query.id,phase,uint64_t(start),uint64_t(due),query.samples);
#else
    (void)trace_owner_;(void)query;(void)phase;
#endif
  }
  void retire() { if (pending_) retired_.push_back({std::move(pending_),position_}); }
  double resolve(const Query& query,bool stale,uint64_t position) {
    trace(query,stale?"wait_stale":"wait");
    double p;
    try { p=query.future.get(); }
    catch(...) {trace(query,"failed");throw;}
    trace(query,stale?"ready_stale":"ready");
    if (!std::isfinite(p) || p<0 || p>1) throw std::runtime_error("invalid semantic completion probability");
    record_({Event::Kind::Resolution,query.id,query.position,position,query.samples,p,stale});
    return p;
  }
  void reap(bool wait) {
    while (!retired_.empty()) {
      const auto& old=retired_.front();
      if (wait) {
        if (old.query->future.wait_for(retirement_)!=std::future_status::ready)
          throw Unretired(retirement_);
      } else if (old.query->future.wait_for(std::chrono::seconds(0))!=std::future_status::ready) return;
      resolve(*old.query,true,old.position);
      retired_.pop_front();
    }
  }
};
}
