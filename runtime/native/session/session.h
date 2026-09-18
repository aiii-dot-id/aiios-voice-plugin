#pragma once
#include "capture_limit.h"
#include <cstdint>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace aii::voice {
struct Cancelled : std::runtime_error { using std::runtime_error::runtime_error; };
// Identification could not read/validate its authoritative enrollment. This is
// not a low match score or acoustic ambiguity; no raw broker error is exposed.
struct EnrollmentUnavailable : std::runtime_error { using std::runtime_error::runtime_error; };
// Private composition boundary, not a second Plugin SDK protocol. The model
// owner outlives Session. Each interface has exactly one inference caller;
// cancel() is the only concurrent entry and must not wait for inference.
struct Recognizer {
  virtual ~Recognizer() = default;
  virtual std::string execution_info() const { return R"({"encoder_provider":"unspecified","hardware_execution_verified":false})"; }
  virtual void open() {} // after all previous session callers have retired
  virtual void begin() = 0;
  virtual std::string push(const float*, size_t) = 0;
  virtual std::string finish() = 0;
  virtual void reset() = 0;
  virtual void cancel() noexcept = 0;
};
struct Vad {
  virtual ~Vad() = default;
  virtual void reset() = 0;
  virtual float score(const float*) = 0; // exactly 512 mono/16k samples
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
  // Initialization owner only, after the prior session has fully retired.
  // A backend must explicitly implement non-default settings, never ignore them.
  virtual void configure(const SpeechSettings& s) {
    if(!s.defaults())throw std::invalid_argument("speech settings unsupported by this backend");
  }
  virtual void start(uint64_t, const std::string&) = 0;
  virtual std::vector<float> next() = 0; // empty only at natural completion
  virtual void reset() = 0;
  virtual void cancel(uint64_t) noexcept = 0;
};
// Optional native speaker owner. The caller supplies the existing enrollment
// projection and frozen policy; Session never enrolls or grants authority.
// identify runs on its own bounded worker, after a final has a sequence. The
// returned data is an observation, not another transcript or turn.
struct SpeakerIdentifier {
  virtual ~SpeakerIdentifier() = default;
  virtual void open() {}
  virtual std::string identify(uint64_t, const std::vector<float>&) = 0;
  virtual void cancel() noexcept = 0;
};
struct Settings {
  uint32_t pause_ms = 768;
  float speech_threshold = .5f;
  uint32_t input_tail_timeout_ms = 3000;
  SpeechSettings speech{};
  uint32_t capture_limit_minutes = default_capture_limit_minutes; // 0: no duration stop
};
struct Event {
  uint64_t sequence = 0, turn = 0, generation = 0, start = 0, end = 0;
  std::string kind, text;
  uint64_t refers_to = 0; // speaker observations refer to this final sequence
};
struct Audio {
  uint64_t generation = 0, start = 0;
  bool end = false;
  std::vector<float> pcm; // mono/24k; host owns conversion and physical render
};
struct Snapshot {
  uint64_t received = 0, controlled = 0, recognized = 0, generation = 0, sequence = 0, cutoff = 0;
  bool input_finished = false, synthesizing = false, draining = false;
  bool stopping = false, retired = false, aborted = false, closing = false, cutoff_set = false;
  bool recognition_active = false;
  size_t queued_audio_samples = 0;
  size_t synthesis_segments = 0, completed_segments = 0;
  std::string error;
};
struct GenerationSnapshot {
  uint64_t generated = 0, delivered = 0, rendered = 0;
  size_t queued = 0;
  bool fenced = false, cancelled = false, retired = false, end_taken = false, receipt = false, stopped = false;
};
class Session {
 public:
  Session(Recognizer&, Vad&, Endpoint&, Synthesizer&, Settings = {}, SpeakerIdentifier* = nullptr);
  ~Session();
  Session(const Session&) = delete;
  Session& operator=(const Session&) = delete;
  // Admission only. false means retryable bounded backpressure; invalid input
  // throws before mutation. No model inference or external callbacks here.
  bool feed(uint64_t start, const float*, size_t);
  void finish_input(uint64_t exclusive_end);
  void synthesize(uint64_t generation, const std::string&);
  void interrupt(uint64_t generation);
  void stop_playback(uint64_t generation);
  void cancel_synthesis(uint64_t generation);
  GenerationSnapshot generation(uint64_t id) const;
  bool event(Event&);
  bool audio(Audio&);
  // Capacity refusal leaves the item in the queue. Control fences can still
  // retire it; callers must retry, not hold detached stale PCM across a fence.
  bool event_bounded(Event&, size_t text_capacity, size_t& required);
  bool audio_bounded(Audio&, size_t sample_capacity, size_t& required);
  // Evidence is in the engine clock. A real adapter must validate/match host
  // receipts before calling this; dequeue is not playback. Terminal reports
  // require consumed END for drained output. A fenced stopped receipt may
  // precede END/compute retirement; neither receipt can later change meaning.
  void playback(uint64_t generation, uint64_t rendered, bool terminal, bool stopped);
  void close(bool abort);
  bool wait_closed(uint32_t milliseconds);
  Snapshot status() const;
 private:
  struct Impl;
  std::unique_ptr<Impl> p_;
};
}
