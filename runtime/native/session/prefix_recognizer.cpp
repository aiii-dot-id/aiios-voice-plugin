#include "prefix_recognizer.h"
#include <algorithm>
#include <cmath>
#include <condition_variable>
#include <exception>
#include <mutex>
#include <thread>

namespace aii::voice {
struct PrefixRecognizer::Impl {
  const PrefixLimits limits;
  std::mutex mutex;
  std::condition_variable changed;
  std::thread worker;
  std::vector<float> pcm;
  std::shared_ptr<std::atomic<bool>> cancelled;
  std::exception_ptr error;
  std::string result;
  size_t wanted=0, completed=0;
  bool ready=false, closing=false, active=false, finishing=false;
  bool processing=false, session_cancelled=false;

  Impl(std::unique_ptr<PrefixDecoder> decoder, PrefixLimits l):limits(l) {
    if(!decoder || !l.minimum_samples || !l.partial_stride_samples ||
        l.minimum_samples>l.maximum_samples || l.maximum_samples>16000u*60u*30u)
      throw std::invalid_argument("explicit valid prefix decoder and sample bounds required");
    // Allocate the bounded accumulator before starting any thread.
    pcm.reserve(l.maximum_samples);
    worker=std::thread([this, d=std::move(decoder)]() mutable {
      try {
        d->open();
        { std::lock_guard<std::mutex> lock(mutex); ready=true; }
        changed.notify_all();
        for(;;) {
          std::vector<float> snapshot;
          std::shared_ptr<std::atomic<bool>> token;
          {
            std::unique_lock<std::mutex> lock(mutex);
            changed.wait(lock,[&]{return closing || (active && cancelled &&
                !cancelled->load() && wanted>completed);});
            if(closing) break;
            // Coalesce requests, not input samples. The copied prefix includes
            // all currently admitted PCM, including a non-hop-aligned tail.
            snapshot=pcm; token=cancelled; processing=true;
          }
          std::string text;
          std::exception_ptr failure;
          bool expected_cancel=false;
          try { text=d->decode(snapshot,*token); }
          catch(const Cancelled&) { failure=std::current_exception(); expected_cancel=token->load(); }
          catch(...) { failure=std::current_exception(); }
          {
            std::lock_guard<std::mutex> lock(mutex);
            processing=false;
            // A cancellation request cannot launder a real accelerator error.
            if(failure && !expected_cancel) error=failure;
            if(!failure && !token->load() && active && token==cancelled) {
              if(text.size()>131071) error=std::make_exception_ptr(
                  std::runtime_error("prefix decoder transcript exceeds bound"));
              else { completed=snapshot.size(); result=std::move(text); }
            }
            if(error) { token->store(true); wanted=0; }
          }
          changed.notify_all();
        }
      } catch(...) {
        { std::lock_guard<std::mutex> lock(mutex); error=std::current_exception(); ready=true; processing=false; }
        changed.notify_all();
      }
      // JNI/LiteRT/Metal thread-affine resources retire on their creation thread.
      d.reset();
    });
    std::unique_lock<std::mutex> lock(mutex);
    changed.wait(lock,[&]{return ready;});
    if(error) {
      const auto failure=error; closing=true; lock.unlock(); changed.notify_all();
      worker.join(); std::rethrow_exception(failure);
    }
  }
  ~Impl() {
    { std::lock_guard<std::mutex> lock(mutex); closing=true; if(cancelled) cancelled->store(true); }
    changed.notify_all(); if(worker.joinable()) worker.join();
  }
  void check_locked() {
    if(error) std::rethrow_exception(error);
    if(session_cancelled || (cancelled && cancelled->load())) throw Cancelled("prefix recognition cancelled");
  }
};
PrefixRecognizer::PrefixRecognizer(std::unique_ptr<PrefixDecoder> d,PrefixLimits l)
    :p_(std::make_unique<Impl>(std::move(d),l)) {}
PrefixRecognizer::~PrefixRecognizer()=default;
void PrefixRecognizer::open() {
  std::lock_guard<std::mutex> lock(p_->mutex);
  if(p_->error) std::rethrow_exception(p_->error);
  if(p_->active || p_->processing) throw std::runtime_error("previous prefix recognition not retired");
  p_->session_cancelled=false;
}
void PrefixRecognizer::begin() {
  std::lock_guard<std::mutex> lock(p_->mutex);
  if(p_->error) std::rethrow_exception(p_->error);
  if(p_->session_cancelled) throw Cancelled("prefix recognition cancelled");
  if(p_->active || p_->processing) throw std::runtime_error("prefix recognition already active");
  p_->pcm.clear(); p_->result.clear(); p_->wanted=p_->completed=0;
  p_->finishing=false; p_->cancelled=std::make_shared<std::atomic<bool>>(false); p_->active=true;
}
std::string PrefixRecognizer::push(const float* samples,size_t n) {
  std::lock_guard<std::mutex> lock(p_->mutex); p_->check_locked();
  if(!p_->active || p_->finishing) throw std::invalid_argument("prefix input is not open");
  if((n && !samples) || n>p_->limits.maximum_samples-p_->pcm.size())
    throw std::invalid_argument("prefix model context exhausted; no truncated transcript");
  for(size_t i=0;i<n;++i) if(!std::isfinite(samples[i]))
    throw std::invalid_argument("nonfinite recognition input");
  if(n) p_->pcm.insert(p_->pcm.end(),samples,samples+n);
  if(p_->pcm.size()>=std::max(p_->limits.minimum_samples,p_->limits.partial_stride_samples) &&
      (!p_->wanted || p_->pcm.size()-p_->wanted>=p_->limits.partial_stride_samples)) {
    p_->wanted=p_->pcm.size(); p_->changed.notify_all();
  }
  return p_->result;
}
std::string PrefixRecognizer::finish() {
  std::unique_lock<std::mutex> lock(p_->mutex); p_->check_locked();
  if(!p_->active) throw std::invalid_argument("prefix recognition is not active");
  p_->finishing=true;
  if(p_->pcm.empty()) return {};
  if(p_->pcm.size()<p_->limits.minimum_samples)
    throw std::invalid_argument("utterance shorter than prefix model minimum; no invented padding");
  p_->wanted=p_->pcm.size(); p_->changed.notify_all();
  p_->changed.wait(lock,[&]{return p_->error || p_->session_cancelled || p_->cancelled->load() ||
      p_->completed==p_->pcm.size();});
  p_->check_locked(); return p_->result;
}
void PrefixRecognizer::reset() {
  std::unique_lock<std::mutex> lock(p_->mutex);
  if(p_->cancelled) p_->cancelled->store(true);
  p_->active=false; p_->wanted=0; p_->changed.notify_all();
  p_->changed.wait(lock,[&]{return !p_->processing;});
  p_->pcm.clear(); p_->result.clear(); p_->completed=0; p_->finishing=false; p_->cancelled.reset();
  if(p_->error) std::rethrow_exception(p_->error);
}
void PrefixRecognizer::cancel() noexcept {
  { std::lock_guard<std::mutex> lock(p_->mutex);
    p_->session_cancelled=true; if(p_->cancelled) p_->cancelled->store(true); }
  p_->changed.notify_all();
}
}
