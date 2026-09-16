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
  std::atomic<bool> cancelled{false};
  std::string text;
  unsigned count = 0;
  void start(uint64_t, const std::string &t) override {
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
struct Models : aii::voice::ModelOwner {
  Asr a;
  Vad v;
  Endpoint e;
  Tts t;
  aii::voice::Recognizer &recognizer() override { return a; }
  aii::voice::Vad &vad() override { return v; }
  aii::voice::Endpoint &endpoint() override { return e; }
  aii::voice::Synthesizer &synthesizer() override { return t; }
  aii_voice_readiness warm() override { return {4, 1, "fixture"}; }
};
} // namespace
extern "C" aii_voice_result aii_voice_models_load(const aii_voice_paths *,
                                                  aii_voice_models **out,
                                                  aii_voice_error *) {
  *out = aii::voice::wrap_models(std::make_unique<Models>());
  return AII_VOICE_OK;
}
extern "C" aii_voice_result aii_voice_models_load_with_backend(const aii_voice_paths *p,
    const char *backend, aii_voice_models **out, aii_voice_error *error) {
  if(!backend || std::string(backend)!="cpu")return AII_VOICE_INVALID;
  return aii_voice_models_load(p,out,error);
}
