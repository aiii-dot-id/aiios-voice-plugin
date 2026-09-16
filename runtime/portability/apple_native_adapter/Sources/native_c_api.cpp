#include "c_api_internal.h"
#include "native_models.h"
#include "startup_trace.h"
#include <cstdio>
#include <chrono>
#include <cmath>
#include <algorithm>
#include <cstring>
#include <cstdlib>
#ifdef AII_WITH_UID
#include "native_speaker.h"
#include "../../native_uid/snapshot.h"
#endif
namespace {
struct Owner final : aii::voice::ModelOwner {
  aii::voice::NativeModels models;
  std::string tts_backend;
#ifdef AII_WITH_UID
  std::unique_ptr<aii::voice::NativeSpeaker> uid;
  aii::uid::PolicyDocument uid_policy;
  aii::voice::SpeakerIdentifier* speaker() override {return uid.get();}
  std::vector<uint64_t> enrollment_finals() override {
    if(!uid)throw std::invalid_argument("native UID model unavailable");
    return uid->enrollment_finals();
  }
  std::string enroll_selected(const std::string& current,const std::string& id,
      const std::string& label,const std::vector<uint64_t>& finals) override {
    if(!uid)throw std::invalid_argument("native UID model unavailable");
    return uid->prepare_selected(current,uid_policy,id,label,finals).snapshot;
  }
#endif
  explicit Owner(const aii::voice::ModelPaths& paths):models(paths),tts_backend(paths.tts_backend) {}
  aii::voice::Recognizer& recognizer() override { return models.recognizer(); }
  aii::voice::Vad& vad() override { return models.vad(); }
  aii::voice::Endpoint& endpoint() override { return models.endpoint(); }
  aii::voice::Synthesizer& synthesizer() override { return models.synthesizer(); }
  aii_voice_readiness warm() override {
    aii::voice::StartupSpan profile("warm_all");
    const auto start=std::chrono::steady_clock::now();
    auto& a=models.recognizer();auto& v=models.vad();auto& e=models.endpoint();auto& t=models.synthesizer();
    std::vector<float> zero(32000,0);a.open();e.open();t.open();
    try {
      a.begin();for(size_t i=0;i<zero.size();i+=2000)a.push(zero.data()+i,2000);
      a.finish();a.reset();v.reset();const auto vp=v.score(zero.data());
      const auto ep=e.score(1,std::vector<float>(16000,0));
      if(!std::isfinite(vp) || vp<0 || vp>1 || !std::isfinite(ep) || ep<0 || ep>1)
        throw std::runtime_error("invalid warm detector probability");
      t.start(1,"Ready.");const auto pcm=t.next();
      if(pcm.empty())throw std::runtime_error("warm synthesis produced no PCM");
      for(float x:pcm)if(!std::isfinite(x))throw std::runtime_error("warm synthesis produced nonfinite PCM");
      t.cancel(1);t.reset();
    } catch(...) { a.cancel();a.reset();t.cancel(1);t.reset();throw; }
#ifdef AII_WITH_UID
    if(uid)uid->warm();
#endif
    const auto ms=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-start).count();
    if(ms>40000)throw std::runtime_error("native warm inference exceeded 40 seconds");
    aii_voice_readiness result{};result.models_loaded=4;result.probe_ms=uint32_t(std::max<int64_t>(1,ms));
#ifdef AII_WITH_UID
    if(uid)result.models_loaded=5;
#endif
    std::snprintf(result.accelerator,sizeof result.accelerator,"%s",tts_backend=="coreml-gpu"?"cpu_coreml_gpu_requested":tts_backend=="vulkan"?"cpu_vulkan":tts_backend=="metal"?"cpu_metal":"cpu");return result;
  }
};
}
extern "C" aii_voice_result aii_voice_models_load(const aii_voice_paths* p,aii_voice_models** out,aii_voice_error* e) {
  return aii_voice_models_load_with_backend(p,"cpu",out,e);
}
extern "C" aii_voice_result aii_voice_models_load_with_backend(const aii_voice_paths* p,const char* backend,aii_voice_models** out,aii_voice_error* e) {
  return aii_voice_models_load_with_uid(p,backend,nullptr,nullptr,0,nullptr,nullptr,out,e);
}
extern "C" aii_voice_result aii_voice_models_load_with_uid(const aii_voice_paths* p,const char* backend,
 const char* uid_model,const char* policy_json,size_t policy_bytes,aii_voice_snapshot_reader reader,void* context,
 aii_voice_models** out,aii_voice_error* e) {
  if(e)e->message[0]=0;
  try {
    if(!p || !out || *out)throw std::invalid_argument("paths and empty model handle required");
    if(!backend || std::strcmp(backend,"coreml-gpu"))
      throw std::invalid_argument("explicit coreml-gpu candidate required; no fallback");
    if(!std::strcmp(backend,"vulkan")) {
      const char* fp32=std::getenv("GGML_VK_DISABLE_F16");
      const char* device=std::getenv("GGML_VK_VISIBLE_DEVICES");
      if(!fp32 || std::strcmp(fp32,"1") || !device || std::strcmp(device,"0"))
        throw std::invalid_argument("Vulkan requires explicit FP32 and device zero process binding");
    }
    for(const auto path:{p->asr,p->mel,p->vad,p->endpoint,p->coefficients,p->pocket,p->pocket_config})
      if(!path || !*path)throw std::invalid_argument("every bound model path required");
    if(!uid_model&&(policy_json||policy_bytes||reader||context))throw std::invalid_argument("partial UID configuration");
    auto owner=std::make_unique<Owner>(aii::voice::ModelPaths{
      p->asr,p->mel,p->vad,p->endpoint,p->coefficients,p->pocket,p->pocket_config,backend});
    if(uid_model) {
#ifdef AII_WITH_UID
      if(!*uid_model||!policy_json||!policy_bytes||policy_bytes>4096||!reader)
        throw std::invalid_argument("bound UID model, policy and snapshot reader required");
      const auto policy=aii::uid::read_policy(std::string(policy_json,policy_bytes));
      owner->uid_policy=policy;
      const char* uid_backend="cpu";
#ifdef AII_MOBILE_COREML_CANDIDATE
      uid_backend="coreml-ane";
#endif
      owner->uid=std::make_unique<aii::voice::NativeSpeaker>(uid_model,uid_backend,policy.policy,[policy,reader,context] {
        std::string bytes(8u<<20,'\0');size_t written=0;
        if(reader(context,bytes.data(),bytes.size(),&written)!=AII_VOICE_OK||!written||written>bytes.size())
          throw std::runtime_error("authoritative UID snapshot unavailable");
        bytes.resize(written);return aii::uid::read_snapshot(bytes,policy);
      });
#else
      throw std::invalid_argument("runtime not linked with native UID");
#endif
    }
    *out=aii::voice::wrap_models(std::move(owner));return AII_VOICE_OK;
  } catch(const std::invalid_argument& ex) { if(e)std::snprintf(e->message,sizeof e->message,"%s",ex.what());return AII_VOICE_INVALID; }
    catch(const std::exception& ex) { if(e)std::snprintf(e->message,sizeof e->message,"%s",ex.what());return AII_VOICE_FAILED; }
    catch(...) { if(e)std::snprintf(e->message,sizeof e->message,"unknown native model load failure");return AII_VOICE_FAILED; }
}
