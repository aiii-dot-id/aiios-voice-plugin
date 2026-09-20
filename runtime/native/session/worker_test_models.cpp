// Only linked into the explicitly named fixture executable, never the engine.
#include "c_api_internal.h"
#include <atomic>
#include <chrono>
#include <thread>
namespace {
struct Asr : aii::voice::Recognizer {
  void begin() override {}
  std::string push(const float *, size_t) override { return "opening words"; }
  std::string finish() override { return "opening words retained"; }
  void reset() override {}
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
  std::atomic<bool> release{false},cancelled{false};
  void open() override {release=false;cancelled=false;}
  std::string identify(uint64_t,const std::vector<float>&) override {
    while(!release && !cancelled)std::this_thread::sleep_for(std::chrono::milliseconds(1));
    if(cancelled)throw aii::voice::Cancelled("fixture UID cancelled");
    return R"({"outcome":"known","speaker_id":"person-a","label":"Fixture speaker","reason":"fixture_match"})";
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
  explicit Models(bool enabled) : uid_enabled(enabled) {t.release_uid=&u.release;}
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
  *out = aii::voice::wrap_models(std::make_unique<Models>(
      paths && paths->asr && std::string(paths->asr)=="fixture-uid"));
  return AII_VOICE_OK;
}
extern "C" aii_voice_result aii_voice_models_load_with_backend(const aii_voice_paths *p,
    const char *backend, aii_voice_models **out, aii_voice_error *error) {
  if(!backend || std::string(backend)!="cpu")return AII_VOICE_INVALID;
  return aii_voice_models_load(p,out,error);
}
