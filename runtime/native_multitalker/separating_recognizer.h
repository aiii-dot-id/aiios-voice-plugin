#pragma once
#include "source_binding.h"
#include <chrono>
#include <memory>

namespace aii::multitalker {
// Private model composition. All inference is serialized by Session; only
// cancel is concurrent. A separator slot is never a person or a durable ID.
struct SourceSeparator {
  virtual ~SourceSeparator() = default;
  virtual size_t maximum_samples() const = 0;
  virtual std::string provider() const = 0;
  virtual void open() = 0;
  virtual Waveforms separate(const std::vector<float>&) = 0;
  virtual void cancel() noexcept = 0;
};
// Separation is best effort. One separator call may use audio_percent of the
// separated audio's duration (5 x), clamped to [minimum, maximum]; this is a
// latency bound below the 30 s model-call watchdog, not a model deadline.
// Expiry cancels only the separator; the turn keeps its unresolved records.
struct SeparationBudget {
  static constexpr uint32_t default_audio_percent=500;
  static constexpr std::chrono::milliseconds default_minimum{4000},default_maximum{25000};
  uint32_t audio_percent=default_audio_percent;
  std::chrono::milliseconds minimum=default_minimum,maximum=default_maximum;
  std::chrono::milliseconds of(size_t samples) const; // 16 kHz input
};
// Why the latest competing turn did or did not receive separated records.
// Fixed tokens in execution_info(), never model messages or paths.
enum class SeparationOutcome:unsigned {none,replaced,incomplete_sources,budget_expired,separator_failed,
  source_failed,window_exceeded,below_minimum,too_many_tracks,count};
class SeparatingRecognizer final:public aii::voice::Recognizer {
 public:
  SeparatingRecognizer(std::unique_ptr<aii::voice::Recognizer> live,
      std::unique_ptr<aii::voice::Recognizer> source,std::unique_ptr<SourceSeparator>,
      SeparationBudget budget={});
  std::string execution_info() const override;
  void open() override;
  void begin() override;
  std::string push(const float*,size_t) override;
  std::string finish() override;
  std::string finish_with_progress(const std::function<void()>&) override;
  bool separated() const override {return true;}
  bool continuous_input() const override {return true;}
  void speech_onset(uint64_t) override;
  std::vector<aii::voice::RecognizedSegment> segments() const override;
  void reset() override;
  void cancel() noexcept override;
  // Both recognizers hear the same speakers and prefer the same terms.
  size_t prefer(const std::vector<std::string>& terms) override {
    if(active_)throw std::runtime_error("previous separating recognizer has not retired");
    source_->prefer(terms);return live_->prefer(terms);
  }
 private:
  std::unique_ptr<aii::voice::Recognizer> live_,source_;
  std::unique_ptr<SourceSeparator> separator_;
  const SeparationBudget budget_;
  std::atomic<bool> cancelled_{false};
  // Read concurrently by execution_info(); written only by the inference owner.
  std::array<std::atomic<uint64_t>,size_t(SeparationOutcome::count)> outcomes_{};
  std::atomic<unsigned> last_outcome_{0};
  std::vector<float> pcm_; // newest capture samples, beginning at origin_
  std::vector<aii::voice::RecognizedSegment> result_;
  uint64_t capture_=0,origin_=0,received_=0,onset_=0;
  bool active_=false,finished_=false,overflow_=false,onset_known_=false;
  void alive() const;
  void record(SeparationOutcome);
};
}
