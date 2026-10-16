// Only linked into the explicitly named fixture executable, never the engine.
#include "c_api_internal.h"
#include "worker_io.h"
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>
#include <thread>
namespace {
std::string fixture_setting(const char *name) {
#ifdef _WIN32
  char *value = nullptr;
  size_t length = 0;
  if (_dupenv_s(&value, &length, name) || !value)
    return {};
  std::string result(value);
  std::free(value);
  return result;
#else
  const char *value = std::getenv(name);
  return value ? value : "";
#endif
}
} // namespace
namespace aii::voice::wire {
// Publication-order seam: hold the audio writer once, after its selected
// write ("pcm", "end" or "failed") and before that Ack is published. A "held"
// file says it is waiting; a "release" file (or ten seconds) lets it publish.
void before_audio_ack(bool end, bool failed) {
  static std::atomic<bool> held{false};
  const auto kind = fixture_setting("AII_FIXTURE_HOLD_AUDIO_ACK");
  if (kind != (failed ? "failed" : end ? "end" : "pcm") || held.exchange(true))
    return;
  const std::filesystem::path gate = fixture_setting("AII_FIXTURE_AUDIO_ACK_GATE");
  std::ofstream(gate / "held").put('1');
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
  while (!std::filesystem::exists(gate / "release") && std::chrono::steady_clock::now() < deadline)
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
}
// Detection-order seam: AII_FIXTURE_AUDIO_DEADLINE=writer leaves an expired
// audio write to the audio writer's own deadline check. A scheduler can
// produce that order; the seam makes it the only one.
bool main_loop_sees_audio_deadline() {
  static const bool main_loop = fixture_setting("AII_FIXTURE_AUDIO_DEADLINE") != "writer";
  return main_loop;
}
// Order seam: AII_FIXTURE_HOLD_AFTER_EVENT_POLL_MS holds each pass of a
// draining session that long between its poll for the core's events and its
// read of the core's status. A scheduler can put the core's last events and
// its retirement in that place; the seam makes it the usual one.
void after_event_poll(bool draining) {
  static const auto hold = fixture_setting("AII_FIXTURE_HOLD_AFTER_EVENT_POLL_MS");
  if (draining && !hold.empty())
    std::this_thread::sleep_for(std::chrono::milliseconds(std::stoi(hold)));
}
// Order seam: AII_FIXTURE_RESET_FAILS_AT names the place on the worker's main
// loop where a "Break." reset fails. "last_status" is between a pass's two
// reads of the core's status, and the pass stays there until the core has
// retired as well; "input" is with a frame in hand, before it is fed. The
// reset waits to fail until the loop is at the place, and the loop waits
// there until the core says it has failed. A scheduler can produce either
// order; the seam makes it the only one, and says in the log that it did.
// No wait here is longer than ten seconds.
namespace {
std::atomic<bool> breaking{false}, reset_waiting{false}, loop_waiting{false};
const std::string &reset_fails_at() {
  static const std::string place = fixture_setting("AII_FIXTURE_RESET_FAILS_AT");
  return place;
}
std::chrono::steady_clock::time_point ten_seconds() {
  return std::chrono::steady_clock::now() + std::chrono::seconds(10);
}
// One step of a bounded wait: true once the deadline has passed.
bool past(std::chrono::steady_clock::time_point deadline) {
  if (std::chrono::steady_clock::now() >= deadline)
    return true;
  std::this_thread::sleep_for(std::chrono::milliseconds(1));
  return false;
}
// The synthesizer's side: a "Break." reply began; and, inside its reset, the
// wait for the loop to be at the place.
void break_began() { breaking = !reset_fails_at().empty(); }
void reset_waits_for_its_place() {
  if (!breaking)
    return;
  const auto deadline = ten_seconds();
  reset_waiting = true;
  while (!loop_waiting && !past(deadline)) {
  }
  reset_waiting = false;
  breaking = false;
}
// The loop's side, at the place: let the reset fail, then wait for the core.
void fail_reset_here(aii_voice_session *session, const char *place, bool retired_too) {
  const auto deadline = ten_seconds();
  aii_voice_snapshot core{};
  aii_voice_error error{};
  const auto failed = [&] { return *core.error && (core.retired || !retired_too); };
  loop_waiting = true;
  while (aii_voice_status(session, &core, &error) == AII_VOICE_OK && !failed() && !past(deadline)) {
  }
  loop_waiting = false;
  if (failed())
    log_line(std::string("AII_FIXTURE_ORDER the reset failed at ") + place);
}
} // namespace
void before_last_status(aii_voice_session *session) {
  if (reset_waiting && reset_fails_at() == "last_status")
    fail_reset_here(session, "last_status", true);
}
void before_input_frame(aii_voice_session *session) {
  if (!session || !breaking || reset_fails_at() != "input")
    return;
  const auto deadline = ten_seconds();
  while (!reset_waiting && !past(deadline)) {
  }
  if (reset_waiting)
    fail_reset_here(session, "input", false);
}
} // namespace aii::voice::wire
namespace {
struct Asr : aii::voice::Recognizer {
  bool separated_track=false;
  std::vector<float> selected;
  // The fixture's stand-in for preferring a term: a one-word term is written
  // as given wherever the fixture's own words hold it without regard to case.
  std::vector<std::string> terms;
  size_t prefer(const std::vector<std::string>& list) override {
    terms.clear();
    for(const auto& term:list)if(!term.empty()&&term.find_first_of(" ,.")==std::string::npos)terms.push_back(term);
    return terms.size();
  }
  std::string written(std::string text) const {
    for(const auto& term:terms) {
      std::string lower=term;for(auto& c:lower)if(c>='A'&&c<='Z')c=char(c-'A'+'a');
      for(size_t at=0;(at=text.find(lower,at))!=std::string::npos;at+=term.size())
        if((!at||text[at-1]==' ')&&(at+lower.size()==text.size()||text[at+lower.size()]==' '))text.replace(at,lower.size(),term);
    }
    return text;
  }
  void begin() override {selected.clear();}
  std::string push(const float *pcm, size_t count) override {
    if(separated_track){selected.insert(selected.end(),pcm,pcm+count);return {};}
    return written("opening words");
  }
  std::string finish() override { return separated_track?"":written("opening words retained"); }
  bool separated() const override {return separated_track;}
  std::vector<aii::voice::RecognizedSegment> segments() const override {
    if(!separated_track)return {};
    return {{"track-a","opening words retained",0,selected.size(),0,selected}};
  }
  void reset() override {selected.clear();}
  void cancel() noexcept override {}
};
// Pause seam: with AII_FIXTURE_VAD=level a block is speech when its first
// sample is loud and silence when it is not, so that a test can end a turn by
// a pause. Without it every block is speech, and a turn ends with its input.
struct Vad : aii::voice::Vad {
  const bool level = fixture_setting("AII_FIXTURE_VAD") == "level";
  void reset() override {}
  float score(const float *p) override { return !level || std::abs(p[0]) > .1f ? .9f : 0.f; }
};
// Slow-verdict seam: AII_FIXTURE_ENDPOINT_MS is how long each verdict takes,
// or until the endpoint is cancelled.
struct Endpoint : aii::voice::Endpoint {
  std::atomic<bool> cancelled{false};
  void open() override { cancelled = false; }
  double score(uint64_t, const std::vector<float> &) override {
    const auto hold = fixture_setting("AII_FIXTURE_ENDPOINT_MS");
    if (!hold.empty()) {
      const auto until = std::chrono::steady_clock::now() + std::chrono::milliseconds(std::stoi(hold));
      while (!cancelled && std::chrono::steady_clock::now() < until)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return .9;
  }
  void cancel() noexcept override { cancelled = true; }
};
struct Tts : aii::voice::Synthesizer {
  // Two voices, so that a change of voice between replies can be shown: the
  // default, and "javert", whose replies are half as long (480 samples for
  // 960). Every other setting is refused, as before; "marius" stays a voice
  // this fixture does not hold. "fantine" passes check and is refused by
  // configure: a preset taken away between the two.
  bool second=false;
  static bool held(const aii::voice::SpeechSettings& s,bool checking) {
    auto base=s;base.voice="alba";
    return base.defaults() && (s.voice=="alba" || s.voice=="javert" || (checking && s.voice=="fantine"));
  }
  void check(const aii::voice::SpeechSettings& s) const override {
    if(!held(s,true))throw std::invalid_argument("speech settings unsupported by this backend");
  }
  void configure(const aii::voice::SpeechSettings& s) override {
    if(!held(s,false))throw std::invalid_argument("speech settings unsupported by this backend");
    second=s.voice=="javert";
  }
  // Open seam: AII_FIXTURE_OPEN_GATE names a directory. Each session's open
  // writes "held" there and returns only once a "release" file is there: an
  // open that takes its time or, never released, a model load that hangs.
  // The open runs on the worker's opening thread and nowhere else.
  void open() override {
    const std::string gate=fixture_setting("AII_FIXTURE_OPEN_GATE");
    if(gate.empty())return;
    const std::filesystem::path dir(gate);
    std::ofstream(dir/"held").put('1');
    while(!std::filesystem::exists(dir/"release"))std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  std::atomic<bool>* release_uid=nullptr;
  std::atomic<bool> cancelled{false};
  std::string text;
  unsigned count = 0;
  void start(uint64_t, const std::string &t) override {
    if(release_uid && t=="Release UID.")*release_uid=true;
    text = t;
    count = 0;
    cancelled = false;
    if (t == "Break.")
      aii::voice::wire::break_began();
  }
  std::vector<float> next() override {
    if (text == "Stall.")
      for (;;) std::this_thread::sleep_for(std::chrono::milliseconds(10));
    // "Break." speaks once before it holds: its audio on the wire says the
    // synthesizer is inside the reply, which synthesis_start does not.
    if (text == "Break." && !count++)
      return std::vector<float>(960, .25f);
    if (text == "Hold." || text == "Break.")
      while (!cancelled)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    if (cancelled)
      throw aii::voice::Cancelled("fixture cancellation");
    if (text == "Flood.")
      return ++count <= 20 ? std::vector<float>(32768, .25f)
                           : std::vector<float>{};
    // Slow-call seam: "Slow." takes AII_FIXTURE_SLOW_CALL_MS for its audio
    // and then ends as any reply does: one model call that is long and returns.
    if (text == "Slow." && !count)
      std::this_thread::sleep_for(std::chrono::milliseconds(std::stoi(fixture_setting("AII_FIXTURE_SLOW_CALL_MS"))));
    return count++ ? std::vector<float>{} : std::vector<float>(second ? 480 : 960, .25f);
  }
  // Engine-fault seam: "Break." speaks, holds like "Hold." and then cannot
  // be restored after its cancel, once. That is an engine failure which
  // arrives while a session is ending, whatever ended the session. Where on
  // the worker's loop it arrives can be stated (AII_FIXTURE_RESET_FAILS_AT).
  void reset() override {
    if (text != "Break.")
      return;
    text.clear();
    aii::voice::wire::reset_waits_for_its_place();
    throw std::runtime_error("fixture synthesizer reset failed");
  }
  void cancel(uint64_t) noexcept override { cancelled = true; }
};
struct Uid : aii::voice::SpeakerIdentifier {
  bool anonymous=false;
  std::atomic<bool> release{false},cancelled{false};
  void open() override {release=false;cancelled=false;}
  std::string identify(uint64_t,const std::vector<float>&) override {
    while(!release && !cancelled)std::this_thread::sleep_for(std::chrono::milliseconds(1));
    if(cancelled)throw aii::voice::Cancelled("fixture UID cancelled");
    return R"({"outcome":"known","speaker_id":"person-a","label":"Fixture speaker","reason":"fixture_match"})";
  }
  std::string identify_track_at(uint64_t,uint64_t,const std::vector<float>& pcm) override {
    if(cancelled)throw aii::voice::Cancelled("fixture UID cancelled");
    if(anonymous)return std::string(R"({"outcome":"unavailable","reason":"acoustic_profile_match","speaker_uuid":"12345678-1234-4234-8234-123456789abc","registry_revision":"3","continuity":"matched","policy_sha256":")")+
      std::string(64,'a')+R"(","embedding_binding":")"+std::string(64,'b')+
      R"(","pcm_sha256":")"+std::string(64,'c')+R"(","samples":)"+std::to_string(pcm.size())+"}";
    return std::string(R"({"outcome":"known","speaker_id":"person-a","label":"Fixture speaker","reason":"fixture_match","enrollment_revision":"1","policy_sha256":")")+
      std::string(64,'a')+R"(","embedding_binding":")"+std::string(64,'b')+
      R"(","pcm_sha256":")"+std::string(64,'c')+R"(","samples":)"+std::to_string(pcm.size())+"}";
  }
  void cancel() noexcept override {cancelled=true;}
};
struct Models : aii::voice::ModelOwner {
  Asr a;
  Vad v;
  Endpoint e;
  Tts t;
  Uid u;
  bool uid_enabled;
  explicit Models(bool enabled,bool separated=false,bool anonymous=false) : uid_enabled(enabled) {
    a.separated_track=separated;u.anonymous=anonymous;t.release_uid=&u.release;
  }
  aii::voice::Recognizer &recognizer() override { return a; }
  aii::voice::Vad &vad() override { return v; }
  aii::voice::Endpoint &endpoint() override { return e; }
  aii::voice::Synthesizer &synthesizer() override { return t; }
  aii::voice::SpeakerIdentifier* speaker() override {return uid_enabled?&u:nullptr;}
  // A worker started with a speaker policy binds its registry here. These
  // models report no track to it.
  void track_observer_at(aii_voice_track_observer_at,void*) override {}
  aii_voice_readiness warm() override { return {uid_enabled?5u:4u, 1, "fixture"}; }
};
} // namespace
extern "C" aii_voice_result aii_voice_models_load(const aii_voice_paths *paths,
                                                  aii_voice_models **out,
                                                  aii_voice_error *) {
  const auto mode=paths&&paths->asr?std::string(paths->asr):"";
  *out = aii::voice::wrap_models(std::make_unique<Models>(
      mode=="fixture-uid"||mode=="fixture-separated-uid"||mode=="fixture-separated-anonymous",
      mode=="fixture-separated-uid"||mode=="fixture-separated-anonymous",mode=="fixture-separated-anonymous"));
  return AII_VOICE_OK;
}
// A fixture started with a speaker policy, as a real worker is: the policy is
// the worker's to read and bind, and these models take none of it.
extern "C" aii_voice_result aii_voice_models_load_uid_policies(const aii_voice_paths *p, const char *backend,
    const char *, const char *, size_t, aii_voice_snapshot_reader, void *, const char *, const char *, size_t,
    aii_voice_models **out, aii_voice_error *error) {
  if(!backend || std::string(backend)!="cpu")return AII_VOICE_INVALID;
  return aii_voice_models_load(p,out,error);
}
extern "C" aii_voice_result aii_voice_models_load_with_backend(const aii_voice_paths *p,
    const char *backend, aii_voice_models **out, aii_voice_error *error) {
  if(!backend || std::string(backend)!="cpu")return AII_VOICE_INVALID;
  return aii_voice_models_load(p,out,error);
}
