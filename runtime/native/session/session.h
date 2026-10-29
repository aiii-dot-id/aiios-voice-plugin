#pragma once
#include "capture_limit.h"
#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>
#include <utility>

namespace aii::voice {
struct Cancelled : std::runtime_error { using std::runtime_error::runtime_error; };
struct EnrollmentUnavailable : std::runtime_error { using std::runtime_error::runtime_error; };
struct SpeakerStorageLate : EnrollmentUnavailable { using EnrollmentUnavailable::EnrollmentUnavailable; };
struct RecognizedSegment {
  std::string track, text;
  uint64_t start=0,end=0;
  uint64_t evidence_start=0;
  std::vector<float> evidence{};
  std::vector<std::pair<uint64_t,uint64_t>> evidence_regions{};
  std::string evidence_unavailable{};
};
struct Recognizer {
  virtual ~Recognizer() = default;
  virtual std::string execution_info() const { return R"({"encoder_provider":"unspecified","hardware_execution_verified":false})"; }
  virtual void open() {}
  virtual void begin() = 0;
  virtual std::string push(const float*, size_t) = 0;
  virtual std::string finish() = 0;
  virtual std::string finish_with_progress(const std::function<void()>&) { return finish(); }
  virtual bool separated() const { return false; }
  virtual bool continuous_input() const { return false; }
  virtual void speech_onset(uint64_t) {}
  virtual std::vector<RecognizedSegment> segments() const { return {}; }
  virtual size_t prefer(const std::vector<std::string>& terms) { (void)terms; return 0; }
  virtual void bound_separation(uint32_t minimum_ms, uint32_t maximum_ms, uint32_t model_call_ms) {
    (void)minimum_ms; (void)maximum_ms; (void)model_call_ms;
  }
  virtual void reset() = 0;
  virtual void cancel() noexcept = 0;
};
struct Vad {
  virtual ~Vad() = default;
  virtual void reset() = 0;
  virtual float score(const float*) = 0;
};
struct Endpoint {
  virtual ~Endpoint() = default;
  virtual void open() {}
  virtual double score(uint64_t, const std::vector<float>&) = 0;
  virtual void cancel() noexcept = 0;
};
struct SpeechSettings {
  std::string voice="alba", tts_language="en", stt_language="en";
  float temperature=.3f;
  uint32_t seed=20260908;
  bool defaults() const { return voice=="alba" && tts_language=="en" && stt_language=="en" && temperature==.3f && seed==20260908; }
};
struct Synthesizer {
  virtual ~Synthesizer() = default;
  virtual void open() {}
  virtual size_t playback_reserve_samples() const { return 0; }
  virtual void configure(const SpeechSettings& s) {
    if(!s.defaults())throw std::invalid_argument("speech settings unsupported by this backend");
  }
  virtual void check(const SpeechSettings& s) const {
    if(!s.defaults())throw std::invalid_argument("speech settings unsupported by this backend");
  }
  virtual void start(uint64_t, const std::string&) = 0;
  virtual std::vector<float> next() = 0;
  virtual bool silent() const { return false; }
  virtual void reset() = 0;
  virtual void cancel(uint64_t) noexcept = 0;
};
struct SpeakerIdentifier {
  virtual ~SpeakerIdentifier() = default;
  virtual void open() {}
  virtual std::string identify(uint64_t, const std::vector<float>&) = 0;
  virtual std::string identify_track(uint64_t, const std::vector<float>&) {
    return R"({"outcome":"unavailable","reason":"speaker_registry_unavailable","used_for_permissions":false})";
  }
  virtual std::string identify_track_at(uint64_t final,uint64_t,const std::vector<float>& pcm) {
    return identify_track(final,pcm);
  }
  virtual void cancel() noexcept = 0;
  std::function<void()> model_part_done;
};
struct Settings {
  uint32_t pause_ms = 768;
  float speech_threshold = .5f;
  uint32_t input_tail_timeout_ms = 3000;
  SpeechSettings speech{};
  uint32_t capture_limit_minutes = default_capture_limit_minutes;
  uint32_t model_call_timeout_ms = 30000;
  uint32_t output_take_timeout_ms = 15000;
  uint32_t endpoint_decision_timeout_ms = 1000;
  uint32_t endpoint_retire_timeout_ms = 30500;
  uint32_t separation_minimum_ms = 0;
  uint32_t separation_maximum_ms = 0;
};
inline constexpr uint32_t session_wait_ceiling_ms = 600000;
inline constexpr uint32_t default_warm_probe_ms = 40000;
struct Hearing {
  Recognizer& recognizer;
  Vad& vad;
  Endpoint& endpoint;
  SpeakerIdentifier* speaker = nullptr;
};
struct Event {
  uint64_t sequence = 0, turn = 0, generation = 0, start = 0, end = 0;
  std::string kind, text;
  uint64_t refers_to = 0;
  std::string track{};
  uint64_t evidence_start = 0, evidence_end = 0;
  bool clean_track = false;
};
struct Audio {
  uint64_t generation = 0, start = 0;
  bool end = false;
  std::vector<float> pcm;
};
struct SpeechState {
  SpeechSettings speech;
  uint64_t revision = 0;
  std::string refused;
};
struct Snapshot {
  bool input_enabled = true;
  uint64_t received = 0, controlled = 0, recognized = 0, generation = 0, sequence = 0, cutoff = 0;
  bool input_finished = false, synthesizing = false, draining = false;
  bool stopping = false, retired = false, aborted = false, closing = false, cutoff_set = false;
  bool recognition_active = false;
  size_t queued_audio_samples = 0;
  size_t synthesis_segments = 0, completed_segments = 0;
  bool model_call_in_flight = false;
  uint64_t model_calls_ended = 0;
  std::string error;
};
struct GenerationSnapshot {
  uint64_t generated = 0, delivered = 0, rendered = 0;
  size_t queued = 0;
  bool fenced = false, cancelled = false, retired = false, end_taken = false, receipt = false, stopped = false;
};
class Session {
 public:
  Session(Synthesizer&, Settings = {}, std::optional<Hearing> = std::nullopt);
  Session(Recognizer&, Vad&, Endpoint&, Synthesizer&, Settings = {}, SpeakerIdentifier* = nullptr);
  ~Session();
  Session(const Session&) = delete;
  Session& operator=(const Session&) = delete;
  bool feed(uint64_t start, const float*, size_t);
  void finish_input(uint64_t exclusive_end);
  void synthesize(uint64_t generation, const std::string&);
  void speech_pending(uint32_t wait_milliseconds);
  void speech(const SpeechSettings&);
  void speech_unchanged();
  SpeechState speech_state() const;
  void interrupt(uint64_t generation);
  void stop_playback(uint64_t generation);
  void cancel_synthesis(uint64_t generation);
  void release_generation(uint64_t generation);
  GenerationSnapshot generation(uint64_t id) const;
  bool event(Event&);
  bool audio(Audio&);
  bool event_bounded(Event&, size_t text_capacity, size_t& required);
  bool audio_bounded(Audio&, size_t sample_capacity, size_t& required);
  void playback(uint64_t generation, uint64_t rendered, bool terminal, bool stopped);
  void close(bool abort);
  bool wait_closed(uint32_t milliseconds);
  Snapshot status() const;
 private:
  struct Impl;
  std::unique_ptr<Impl> p_;
};
}
