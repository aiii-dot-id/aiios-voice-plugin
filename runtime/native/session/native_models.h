#pragma once
#include "session.h"
#include "admission.h"
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
  struct SessionHearing {
    std::unique_ptr<Recognizer> recognizer;
    std::unique_ptr<Vad> vad;
    std::unique_ptr<Endpoint> endpoint;
  };
  SessionHearing open_hearing();
  std::unique_ptr<Recognizer> open_recognizer();
  std::unique_ptr<Endpoint> open_endpoint();
  std::unique_ptr<Vad> open_vad();
  std::unique_ptr<Synthesizer> open_speech();
#if defined(__linux__) && !defined(__ANDROID__)
  std::string tts_execution_info();
#endif
  static std::unique_ptr<Vad> load_vad(const std::string& path);
  static LaneCosts speech_lane_costs();
  static void forget_speech_lane_costs();
 private:
  struct Impl;
  std::unique_ptr<Impl> p_;
};
}
