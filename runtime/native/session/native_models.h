#pragma once
#include "session.h"
#include <memory>

namespace aii::voice {
// Asset bytes/paths come from the caller's verified immutable inventory. This
// layer performs no downloading or provider fallback. TTS placement is explicit;
// recognition/detector placement remains the qualified CPU component profile.
struct ModelPaths {
  std::string asr, mel, vad, endpoint, coefficients, pocket, pocket_config;
  std::string tts_backend = "cpu";
  std::string asr_execution{};
};
class NativeModels {
 public:
  explicit NativeModels(const ModelPaths&);
  // Private platform composition. A supplied recognizer is owned here and
  // prevents construction/loading of the default ASR, not merely its use.
  NativeModels(const ModelPaths&, std::unique_ptr<Recognizer>);
  ~NativeModels();
  NativeModels(const NativeModels&)=delete;
  NativeModels& operator=(const NativeModels&)=delete;
  Recognizer& recognizer();
  Vad& vad();
  Endpoint& endpoint();
  Synthesizer& synthesizer();
#if defined(__linux__) && !defined(__ANDROID__)
  std::string tts_execution_info();
#endif
 private:
  struct Impl;
  std::unique_ptr<Impl> p_;
};
}
