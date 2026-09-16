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
    enum class Kind { Query, Resolution } kind;
    uint64_t query_id, query_position, resolution_position;
    size_t samples;
    double probability;
    bool stale;
  };
  using Record=std::function<void(const Event&)>;
  enum class Decision { None, SemanticNoHold, BoundedSilence };
  static constexpr uint64_t trigger_samples=10240, commitment_samples=12288, maximum_silence_samples=30720;
  static constexpr double hold_threshold=.01;
  static constexpr auto timeout=std::chrono::milliseconds(250);

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
    if (silence>=trigger_ && !checked_ && !pending_) {
      reap(true);
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
      const auto probability=resolve(*pending_,false,position);
      pending_.reset(); checked_=true;
      if (probability>hold_threshold) return Decision::SemanticNoHold;
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
    if (query.future.wait_until(query.started+timeout)!=std::future_status::ready) {
      trace(query,"timeout");
      throw std::runtime_error("semantic endpoint exceeded 250ms; no turn committed");
    }
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
      if (!wait && old.query->future.wait_for(std::chrono::seconds(0))!=std::future_status::ready) return;
      resolve(*old.query,true,old.position);
      retired_.pop_front();
    }
  }
};
}
