#include "separating_recognizer.h"
#include <algorithm>
#include <cmath>
#include <condition_variable>
#include <limits>
#include <mutex>
#include <thread>

namespace aii::multitalker {
namespace {
constexpr uint64_t minimum_samples=32000; // qualified separator floor
bool competing(const aii::voice::RecognizedSegment& row) {
  return row.evidence_unavailable=="speaker_overlap_without_isolated_evidence"||
    row.evidence_unavailable=="speaker_track_coverage_unverified";
}
constexpr const char* outcome_names[]={"none","replaced","incomplete_sources","budget_expired",
  "separator_failed","source_failed","window_exceeded","below_minimum","too_many_tracks"};
static_assert(std::size(outcome_names)==size_t(SeparationOutcome::count),"separation outcome names");
// Runs work under a latency budget. On expiry stop() runs on a timer thread;
// the caller sees expired only after that thread has retired.
template<class Work,class Stop> auto bounded(std::chrono::milliseconds budget,Work work,Stop stop,bool& expired) {
  std::mutex mutex;std::condition_variable changed;bool done=false;
  std::thread timer([&]{
    std::unique_lock<std::mutex> lock(mutex);
    if(changed.wait_for(lock,budget,[&]{return done;}))return;
    expired=true;lock.unlock();stop();
  });
  struct Retire {
    std::mutex& mutex;std::condition_variable& changed;bool& done;std::thread& timer;
    ~Retire(){{std::lock_guard<std::mutex> lock(mutex);done=true;}changed.notify_all();timer.join();}
  } retire{mutex,changed,done,timer};
  return work();
}
}
std::chrono::milliseconds SeparationBudget::of(size_t samples) const {
  return std::clamp(std::chrono::milliseconds(uint64_t(samples)*audio_percent/1600),minimum,maximum);
}
SeparatingRecognizer::SeparatingRecognizer(std::unique_ptr<aii::voice::Recognizer> live,
    std::unique_ptr<aii::voice::Recognizer> source,std::unique_ptr<SourceSeparator> separator,SeparationBudget budget)
    :live_(std::move(live)),source_(std::move(source)),separator_(std::move(separator)),budget_(budget) {
  if(!live_||!source_||!separator_||!live_->separated()||!live_->continuous_input()||
     !source_->separated()||separator_->maximum_samples()<32000||separator_->maximum_samples()>480000)
    throw std::invalid_argument("source separation composition differs");
  if(!budget_.audio_percent||budget_.audio_percent>10000||budget_.minimum.count()<=0||
     budget_.minimum>budget_.maximum||budget_.maximum>=std::chrono::seconds(30))
    throw std::invalid_argument("source separation budget");
}
void SeparatingRecognizer::alive() const {
  if(cancelled_.load())throw aii::voice::Cancelled("source separation cancelled");
}
std::string SeparatingRecognizer::execution_info() const {
  // Both components produce fixed, private diagnostics, never model paths.
  // Outcome counters cover turns whose live rows reported competition.
  std::string outcomes;
  for(size_t i=1;i<outcomes_.size();++i)
    outcomes+=std::string(i>1?",":"")+"\""+outcome_names[i]+"\":"+std::to_string(outcomes_[i].load());
  return "{\"recognizer\":"+live_->execution_info()+",\"source_separator\":\""+
    separator_->provider()+"\",\"separator_maximum_samples\":"+
    std::to_string(separator_->maximum_samples())+",\"separation_budget\":{\"audio_percent\":"+
    std::to_string(budget_.audio_percent)+",\"minimum_ms\":"+std::to_string(budget_.minimum.count())+
    ",\"maximum_ms\":"+std::to_string(budget_.maximum.count())+"},\"separation_outcomes\":{"+outcomes+
    "},\"last_separation_outcome\":\""+outcome_names[last_outcome_.load()]+"\",\"hardware_execution_verified\":false}";
}
void SeparatingRecognizer::record(SeparationOutcome outcome) {
  ++outcomes_[size_t(outcome)];last_outcome_.store(unsigned(outcome));
}
void SeparatingRecognizer::open() {
  if(active_)throw std::runtime_error("previous separating recognizer has not retired");
  cancelled_.store(false);live_->open();alive();separator_->open();alive();
}
void SeparatingRecognizer::begin() {
  alive();
  if(active_||capture_==std::numeric_limits<uint64_t>::max())
    throw std::runtime_error("separating recognizer lifecycle");
  result_.clear();pcm_.clear();origin_=received_=onset_=0;overflow_=finished_=onset_known_=false;++capture_;active_=true;
  live_->begin();alive();
}
std::string SeparatingRecognizer::push(const float* pcm,size_t count) {
  alive();if(!active_||finished_)throw std::runtime_error("separating recognizer is not accepting speech");
  if(!pcm&&count)throw std::invalid_argument("missing separation PCM");
  if(count>std::numeric_limits<uint64_t>::max()-received_)throw std::overflow_error("separation sample clock");
  for(size_t i=0;i<count;++i)if(!std::isfinite(pcm[i])||std::abs(pcm[i])>1)
    throw std::invalid_argument("separation PCM range");
  // Retain only the newest bounded window. Silence before a named speech onset
  // may leave it; the turn from its onset preroll must not.
  const uint64_t end=received_+count,limit=separator_->maximum_samples();
  const auto first=std::max<uint64_t>(origin_,end>limit?end-limit:0);
  if(!overflow_&&onset_known_&&first>onset_){overflow_=true;std::vector<float>().swap(pcm_);}
  if(!overflow_&&count) {
    const auto old=std::min<uint64_t>(first-origin_,pcm_.size());
    pcm_.erase(pcm_.begin(),pcm_.begin()+size_t(old));
    pcm_.insert(pcm_.end(),pcm+(first-origin_-old),pcm+count);origin_=first;
  }
  received_=end;
  if(!live_->push(pcm,count).empty())throw std::runtime_error("separating recognizer received pooled partial");
  alive();return {};
}
void SeparatingRecognizer::speech_onset(uint64_t offset) {
  alive();if(!active_||finished_||onset_known_)throw std::runtime_error("separating recognizer speech onset lifecycle");
  if(offset>received_)throw std::invalid_argument("speech onset after captured input");
  // A preroll older than the retained window belongs to a turn that cannot be
  // separated whole; it keeps the unresolved records.
  onset_known_=true;onset_=offset;
  if(!overflow_&&offset<origin_){overflow_=true;std::vector<float>().swap(pcm_);}
  live_->speech_onset(offset);alive();
}
std::string SeparatingRecognizer::finish() {
  return finish_with_progress({});
}
std::string SeparatingRecognizer::finish_with_progress(const std::function<void()>& completed) {
  alive();if(!active_||finished_)throw std::runtime_error("separating recognizer is not accepting speech");
  if(!live_->finish_with_progress(completed).empty())throw std::runtime_error("separating recognizer received pooled final");
  alive();auto rows=live_->segments();
  if(completed)completed();
  bool competition=false;
  for(const auto& row:rows)competition=competition||competing(row);
  // An announced turn starts at its speech preroll, not the previous turn's
  // end; one shorter than the qualified floor reuses earlier capture, as before.
  // Do not truncate a longer turn or force more than two detected tracks
  // through a two-source model. Retain its existing unresolved transcript.
  const auto window=onset_known_?std::max(origin_,std::min(onset_,received_-std::min(received_,minimum_samples))):0;
  if(!competition) {}
  else if(overflow_||window<origin_)record(SeparationOutcome::window_exceeded);
  else if(received_-window<minimum_samples)record(SeparationOutcome::below_minimum);
  else if(rows.size()>2)record(SeparationOutcome::too_many_tracks);
  else {
    pcm_.erase(pcm_.begin(),pcm_.begin()+size_t(window-origin_));origin_=window;
    bool refused=false,binding=false,expired=false;
    auto outcome=SeparationOutcome::incomplete_sources;
    const std::function<void()> stage=[&]{if(completed)try{completed();}catch(...){refused=true;throw;}};
    try {
      alive();
      const auto waveforms=bounded(budget_.of(pcm_.size()),[&]{return separator_->separate(pcm_);},
                                   [this]{separator_->cancel();},expired);
      // A result that arrives after its budget is abandoned with that budget.
      if(expired)throw aii::voice::Cancelled("separation budget expired");
      alive();
      for(const auto& source:waveforms) {
        if(source.size()!=pcm_.size())throw std::runtime_error("separator source extent");
        for(float x:source)if(!std::isfinite(x)||std::abs(x)>1)throw std::runtime_error("separator source PCM");
      }
      stage();binding=true;
      auto separated=bind_source_text(*source_,waveforms,capture_,cancelled_,stage);alive();
      // Losing every word is not a successful replacement. Retain the original
      // unresolved records rather than inventing a silent completed utterance.
      bool first=false,second=false;
      const auto prefix="capture-"+std::to_string(capture_)+".source-";
      for(const auto& row:separated) {
        first=first||row.track.compare(0,prefix.size()+2,prefix+"0.")==0;
        second=second||row.track.compare(0,prefix.size()+2,prefix+"1.")==0;
      }
      if(first&&second) {
        // Sources share the window's clock; report it on the capture clock.
        for(auto& row:separated) {
          row.start+=window;row.end+=window;row.evidence_start+=window;
          for(auto& region:row.evidence_regions){region.first+=window;region.second+=window;}
        }
        rows=std::move(separated);outcome=SeparationOutcome::replaced;
      }
    } catch(...) {
      // The live transcript is already complete. A separator past its budget
      // or a separator or source model that failed to run (memory, provider,
      // non-finite output) keeps it with a typed reason and no model message.
      // Session cancellation, the caller's progress refusal and an invalid
      // source result still fail the turn.
      if(refused)throw;
      alive();
      try{throw;}catch(const aii::voice::Cancelled&){if(!expired||binding)throw;}
      catch(const SourceContractViolation&){if(binding)throw;}catch(const std::exception&){}
      outcome=expired?SeparationOutcome::budget_expired:binding?SeparationOutcome::source_failed:
                                                                SeparationOutcome::separator_failed;
      source_->reset();
      for(auto& row:rows)if(competing(row))row.evidence_unavailable="speaker_separation_failed";
    }
    // Only the budget cancelled the separator, whose call has returned: re-arm
    // it for the next turn. A later session cancel still ends this turn below.
    if(expired)separator_->open();
    record(outcome);
  }
  alive();result_=std::move(rows);std::vector<float>().swap(pcm_);finished_=true;return {};
}
std::vector<aii::voice::RecognizedSegment> SeparatingRecognizer::segments() const {
  alive();if(!finished_)throw std::runtime_error("separated finals are not ready");return result_;
}
void SeparatingRecognizer::reset() {
  live_->reset();source_->reset();pcm_.clear();result_.clear();origin_=received_=onset_=0;
  active_=finished_=overflow_=onset_known_=false;
}
void SeparatingRecognizer::cancel() noexcept {
  cancelled_.store(true);live_->cancel();source_->cancel();separator_->cancel();
}
}
