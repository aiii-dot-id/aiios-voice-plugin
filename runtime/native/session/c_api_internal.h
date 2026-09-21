#pragma once
#include "c_api.h"
#include "session.h"
namespace aii::voice {
// Private C++ injection seam used by the real adapter and model-free tests.
// There is one exclusive active-session lease per resident model owner.
struct ModelOwner {
  virtual ~ModelOwner() = default;
  virtual Recognizer& recognizer() = 0;
  virtual Vad& vad() = 0;
  virtual Endpoint& endpoint() = 0;
  virtual Synthesizer& synthesizer() = 0;
  virtual SpeakerIdentifier* speaker() { return nullptr; }
  virtual void track_observer(aii_voice_track_observer,void*) {
    throw std::invalid_argument("speaker registry unavailable in this model owner");
  }
  virtual aii_voice_capture prepare_capture(const std::vector<float>&) {
    throw std::invalid_argument("native capture preparation unavailable in this model owner");
  }
  virtual std::string enroll_selected(const std::string&,const std::string&,
      const std::string&,const std::vector<uint64_t>&) {
    throw std::invalid_argument("native enrollment is unavailable in this model owner");
  }
  virtual std::vector<uint64_t> enrollment_finals() {
    throw std::invalid_argument("native enrollment evidence unavailable");
  }
  virtual aii_voice_readiness warm() { throw std::runtime_error("model owner has no readiness implementation"); }
  virtual std::string execution_info() { return R"({"scope":"unspecified model owner","hardware_execution_verified":false})"; }
};
aii_voice_models* wrap_models(std::unique_ptr<ModelOwner>);
// Private platform factory; not an added public SDK or serialized ABI. The
// supplied recognizer replaces construction of the default ASR. Its lifetime
// and exclusive session lease are the same as every other resident component.
aii_voice_result load_native_models(const aii_voice_paths*,const char* tts_backend,
    const char* uid_model,const char* policy_json,size_t policy_bytes,
    aii_voice_snapshot_reader,void* context,aii_voice_models**,aii_voice_error*,
    std::unique_ptr<Recognizer> recognizer,const char* asr_execution=nullptr,
    const char* previous_policy=nullptr,size_t previous_policy_bytes=0);
}
