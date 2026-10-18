#pragma once
#include "session.h"
#include <memory>

namespace aii::voice {
struct ModelPaths {
  std::string asr, mel, vad, endpoint, coefficients, pocket, pocket_config;
  std::string tts_backend = "cpu";
  std::string asr_execution{};
};
class NativeModels {
 public:
  explicit NativeModels(const ModelPaths&);
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
