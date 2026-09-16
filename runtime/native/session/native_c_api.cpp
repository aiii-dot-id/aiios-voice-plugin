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
#include "../../native_uid/bound_policies.h"
#endif
namespace {
struct Owner final : aii::voice::ModelOwner {
  const bool supplied_recognizer;
  aii::voice::NativeModels models;
  std::string tts_backend;
#ifdef AII_WITH_UID
  std::unique_ptr<aii::voice::NativeSpeaker> uid;
  std::optional<aii::uid::BoundPolicies> uid_policies;
  aii::voice::SpeakerIdentifier* speaker() override {return uid.get();}
  aii_voice_capture prepare_capture(const std::vector<float>& pcm) override {
    if(!uid)throw std::invalid_argument("native UID model unavailable");
    const auto sample=uid->prepare_capture(pcm);
    aii_voice_capture result{};result.samples=pcm.size();
    std::snprintf(result.embedding_binding,sizeof result.embedding_binding,"%s",uid_policies->current().policy.embedding_binding.c_str());
    std::snprintf(result.pcm_sha256,sizeof result.pcm_sha256,"%s",sample.audio_sha256.c_str());
    std::copy(sample.embedding.begin(),sample.embedding.end(),result.embedding);
    return result;
  }
  std::vector<uint64_t> enrollment_finals() override {
    if(!uid)throw std::invalid_argument("native UID model unavailable");
    return uid->enrollment_finals();
  }
  std::string enroll_selected(const std::string& current,const std::string& id,
      const std::string& label,const std::vector<uint64_t>& finals) override {
    if(!uid)throw std::invalid_argument("native UID model unavailable");
    return uid->prepare_selected(current,uid_policies->resolve(current),id,label,finals).snapshot;
  }
#endif
  Owner(const aii::voice::ModelPaths& paths,std::unique_ptr<aii::voice::Recognizer> supplied)
      :supplied_recognizer(bool(supplied)),models(paths,std::move(supplied)),tts_backend(paths.tts_backend) {}
  aii::voice::Recognizer& recognizer() override { return models.recognizer(); }
  aii::voice::Vad& vad() override { return models.vad(); }
  aii::voice::Endpoint& endpoint() override { return models.endpoint(); }
  aii::voice::Synthesizer& synthesizer() override { return models.synthesizer(); }
  std::string execution_info() override {
#ifdef AII_MOBILE_COREML_CANDIDATE
    const std::string detector="CoreMLExecutionProvider";
#else
    const std::string detector="CPUExecutionProvider";
#endif
    // Configuration and selection only; never turn registration into an NPU
    // or all-GPU execution claim. Actual placement is measured independently.
#if defined(__linux__) && !defined(__ANDROID__)
    const auto tts=models.tts_execution_info();
#else
    const auto tts="{\"requested_backend\":\""+tts_backend+"\",\"hardware_execution_verified\":false}";
#endif
    return "{\"asr\":"+models.recognizer().execution_info()+
      ",\"tts\":"+tts+","+
      "\"vad\":{\"provider\":\""+detector+"\",\"hardware_execution_verified\":false},"+
      "\"endpoint\":{\"provider\":\""+detector+"\",\"hardware_execution_verified\":false},"+
      "\"uid\":{\"provider\":\""+(speaker()?detector:"not_loaded")+"\",\"hardware_execution_verified\":false}}";
  }
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
    // A replacement recognizer's placement is not inferred from its interface.
    // Platform-specific measured readiness must name that backend separately.
    std::snprintf(result.accelerator,sizeof result.accelerator,"%s",supplied_recognizer?"external_recognizer":tts_backend=="vulkan"?"cpu_vulkan":tts_backend=="metal"?"cpu_metal":"cpu");return result;
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
  return aii::voice::load_native_models(p,backend,uid_model,policy_json,policy_bytes,reader,context,out,e,nullptr);
}
extern "C" aii_voice_result aii_voice_models_load_configured(const aii_voice_paths* p,const char* backend,
 const char* uid_model,const char* policy_json,size_t policy_bytes,aii_voice_snapshot_reader reader,void* context,
 const char* execution,aii_voice_models** out,aii_voice_error* e) {
  return aii::voice::load_native_models(p,backend,uid_model,policy_json,policy_bytes,reader,context,out,e,nullptr,execution);
}
aii_voice_result aii::voice::load_native_models(const aii_voice_paths* p,const char* backend,
 const char* uid_model,const char* policy_json,size_t policy_bytes,aii_voice_snapshot_reader reader,void* context,
 aii_voice_models** out,aii_voice_error* e,std::unique_ptr<Recognizer> supplied,const char* execution,
 const char* previous_policy,size_t previous_bytes) {
  if(e)e->message[0]=0;
  try {
    if(!p || !out || *out)throw std::invalid_argument("paths and empty model handle required");
    if(!backend || (std::strcmp(backend,"cpu") && std::strcmp(backend,"vulkan") && std::strcmp(backend,"metal")))
      throw std::invalid_argument("explicit cpu, vulkan or metal TTS backend required; no fallback");
    if(!std::strcmp(backend,"vulkan")) {
      const char* fp32=std::getenv("GGML_VK_DISABLE_F16");
#if defined(__linux__) && !defined(__ANDROID__)
      if(!fp32 || std::strcmp(fp32,"1"))
        throw std::invalid_argument("Vulkan requires explicit FP32 process binding");
#else
      const char* device=std::getenv("GGML_VK_VISIBLE_DEVICES");
      if(!fp32 || std::strcmp(fp32,"1") || !device || std::strcmp(device,"0"))
        throw std::invalid_argument("Vulkan requires explicit FP32 and device zero process binding");
#endif
    }
    if(!supplied && (!p->asr || !*p->asr))throw std::invalid_argument("bound ASR path required");
    for(const auto path:{p->mel,p->vad,p->endpoint,p->coefficients,p->pocket,p->pocket_config})
      if(!path || !*path)throw std::invalid_argument("every bound model path required");
    if(!uid_model&&(policy_json||policy_bytes||reader||context||previous_policy||previous_bytes))throw std::invalid_argument("partial UID configuration");
    if((previous_policy==nullptr)!=(previous_bytes==0)||previous_bytes>4096)
      throw std::invalid_argument("bounded previous UID policy required");
    std::string requested;
    if(execution) {
      size_t n=0;while(n<=2048&&execution[n])++n;
      if(!n||n>2048||supplied)throw std::invalid_argument("bounded ASR configuration without injected recognizer required");
      requested.assign(execution,n);
    }
    auto owner=std::make_unique<Owner>(aii::voice::ModelPaths{
      p->asr?p->asr:"",p->mel,p->vad,p->endpoint,p->coefficients,p->pocket,p->pocket_config,backend,requested},std::move(supplied));
    if(uid_model) {
#ifdef AII_WITH_UID
      if(!*uid_model||!policy_json||!policy_bytes||policy_bytes>4096||!reader)
        throw std::invalid_argument("bound UID model, policy and snapshot reader required");
      const aii::uid::BoundPolicies policies(std::string(policy_json,policy_bytes),
          previous_policy?std::string(previous_policy,previous_bytes):std::string{});
      const auto& policy=policies.current();owner->uid_policies=policies;
      const char* uid_backend="cpu";
#ifdef AII_MOBILE_COREML_CANDIDATE
      uid_backend="coreml-ane";
#endif
      owner->uid=std::make_unique<aii::voice::NativeSpeaker>(uid_model,uid_backend,policy.policy,[policies,reader,context] {
        std::string bytes(8u<<20,'\0');size_t written=0;
        if(reader(context,bytes.data(),bytes.size(),&written)!=AII_VOICE_OK||!written||written>bytes.size())
          throw std::runtime_error("authoritative UID snapshot unavailable");
        bytes.resize(written);return policies.read(bytes);
      },policies.previous()?std::optional<aii::uid::Policy>(policies.previous()->policy):std::nullopt);
#else
      throw std::invalid_argument("runtime not linked with native UID");
#endif
    }
    *out=aii::voice::wrap_models(std::move(owner));return AII_VOICE_OK;
  } catch(const std::invalid_argument& ex) { if(e)std::snprintf(e->message,sizeof e->message,"%s",ex.what());return AII_VOICE_INVALID; }
    catch(const std::exception& ex) { if(e)std::snprintf(e->message,sizeof e->message,"%s",ex.what());return AII_VOICE_FAILED; }
    catch(...) { if(e)std::snprintf(e->message,sizeof e->message,"unknown native model load failure");return AII_VOICE_FAILED; }
}
extern "C" aii_voice_result aii_voice_models_load_uid_policies(const aii_voice_paths* p,const char* backend,
 const char* uid_model,const char* policy_json,size_t policy_bytes,aii_voice_snapshot_reader reader,void* context,
 const char* execution,const char* previous_policy,size_t previous_bytes,aii_voice_models** out,aii_voice_error* e) {
  return aii::voice::load_native_models(p,backend,uid_model,policy_json,policy_bytes,reader,context,out,e,nullptr,
      execution,previous_policy,previous_bytes);
}
