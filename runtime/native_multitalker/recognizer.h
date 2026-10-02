#pragma once
#include "microphone.h"
#include "evidence_audio.h"
#include "../native/session/session.h"

namespace aii::multitalker {
// Native resident-session adapter. The graph and vocabulary inventory must be
// verified by the model owner before construction, as for the other engines.
class Recognizer final:public aii::voice::Recognizer {
 public:
  Recognizer(const std::string& graphs,const float* mel,size_t count,std::vector<std::string> vocabulary,const NemotronConfig& config={},const EncoderExecution& execution={});
  std::string execution_info() const override;
  void open() override;
  void begin() override;
  std::string push(const float*,size_t) override;
  std::string finish() override;
  std::string finish_with_progress(const std::function<void()>&) override;
  bool separated() const override {return true;}
  bool continuous_input() const override {return true;}
  std::vector<aii::voice::RecognizedSegment> segments() const override;
  void reset() override;
  void cancel() noexcept override;
 private:
  Microphone microphone_;
  SpeakerEvidence evidence_;
  EvidenceAudio evidence_audio_;
  std::vector<std::string> vocabulary_;
  std::array<std::string,track_count> text_;
  bool nemotron_=false;
  bool encoder_cuda_=false;
  uint64_t epoch_=0,samples_=0;
  bool active_=false,finished_=false,fresh_capture_=true;
  std::atomic<bool> cancelled_{false};
  void append(const std::vector<MicrophoneUpdate>&);
};
}
