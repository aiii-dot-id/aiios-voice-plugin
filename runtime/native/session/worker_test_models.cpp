// Only linked into the explicitly named fixture executable, never the engine.
#include "c_api_internal.h"
#include <atomic>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <string>
#include <thread>
namespace aii::voice::wire {
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
} // namespace aii::voice::wire
namespace {
struct Asr : aii::voice::Recognizer {
  bool separated_track=false;
  std::vector<float> selected;
  void begin() override {selected.clear();}
  std::string push(const float *pcm, size_t count) override {
    if(separated_track){selected.insert(selected.end(),pcm,pcm+count);return {};}
    return "opening words";
  }
  std::string finish() override { return separated_track?"":"opening words retained"; }
  bool separated() const override {return separated_track;}
  std::vector<aii::voice::RecognizedSegment> segments() const override {
    if(!separated_track)return {};
    return {{"track-a","opening words retained",0,selected.size(),0,selected}};
  }
  void reset() override {selected.clear();}
  void cancel() noexcept override {}
};
struct Vad : aii::voice::Vad {
  void reset() override {}
  float score(const float *) override { return .9f; }
};
struct Endpoint : aii::voice::Endpoint {
  double score(uint64_t, const std::vector<float> &) override { return .9; }
  void cancel() noexcept override {}
};
struct Tts : aii::voice::Synthesizer {
  std::atomic<bool>* release_uid=nullptr;
  std::atomic<bool> cancelled{false};
  std::string text;
  unsigned count = 0;
  void start(uint64_t, const std::string &t) override {
    if(release_uid && t=="Release UID.")*release_uid=true;
    text = t;
    count = 0;
    cancelled = false;
  }
  std::vector<float> next() override {
    if (text == "Stall.")
      for (;;) std::this_thread::sleep_for(std::chrono::milliseconds(10));
    if (text == "Hold.")
      while (!cancelled)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    if (cancelled)
      throw aii::voice::Cancelled("fixture cancellation");
    if (text == "Flood.")
      return ++count <= 20 ? std::vector<float>(32768, .25f)
                           : std::vector<float>{};
    return count++ ? std::vector<float>{} : std::vector<float>(960, .25f);
  }
  void reset() override {}
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
extern "C" aii_voice_result aii_voice_models_load_with_backend(const aii_voice_paths *p,
    const char *backend, aii_voice_models **out, aii_voice_error *error) {
  if(!backend || std::string(backend)!="cpu")return AII_VOICE_INVALID;
  return aii_voice_models_load(p,out,error);
}
