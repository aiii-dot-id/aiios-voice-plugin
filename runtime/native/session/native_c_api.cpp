#include "c_api_internal.h"
#include "native_models.h"
#include "startup_trace.h"
#include "uid_backend.h"
#include "registered_accelerators.h"
#include <cstdio>
#include <chrono>
#include <cmath>
#include <algorithm>
#include <cstring>
#include <cstdlib>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include "model_server_control.h"
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <unistd.h>
#endif
#ifdef AII_WITH_UID
#include "native_speaker.h"
#include "../../native_uid/snapshot.h"
#include "../../native_uid/bound_policies.h"
#endif
namespace {
std::optional<std::string> environment(const char* name) {
#ifdef _WIN32
  char* value=nullptr;size_t length=0;
  if(_dupenv_s(&value,&length,name))throw std::runtime_error(std::string("cannot read ")+name);
  std::unique_ptr<char,decltype(&std::free)> owned(value,std::free);
#else
  const char* value=std::getenv(name);
#endif
  if(!value)return std::nullopt;
  return std::string(value);
}
aii_voice_readiness warm_parts(aii::voice::Recognizer& a,aii::voice::Vad& v,aii::voice::Endpoint& e,
    aii::voice::Synthesizer& t,aii::voice::SpeakerIdentifier* speaker) {
  aii::voice::StartupSpan profile("warm_all");
  const auto start=std::chrono::steady_clock::now();
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
  if(speaker)static_cast<aii::voice::NativeSpeaker*>(speaker)->warm();
#endif
  const auto ms=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-start).count();
  aii_voice_readiness result{};result.models_loaded=speaker?5:4;result.probe_ms=uint32_t(std::max<int64_t>(1,ms));
  return result;
}
struct SpeakerOwner : aii::voice::ModelOwner {
#ifdef AII_WITH_UID
  std::unique_ptr<aii::voice::NativeSpeaker> uid;
  std::optional<aii::uid::BoundPolicies> uid_policies;
  aii::voice::SpeakerIdentifier* speaker() override {return uid.get();}
  void track_observer_at(aii_voice_track_observer_at callback,void* context) override {
    if(!uid||!callback||!context)throw std::invalid_argument("native UID observer unavailable");
    const auto binding=uid_policies->current().policy.embedding_binding;
    uid->track_observer_at([callback,context,binding](uint64_t session,uint64_t utterance,const std::optional<aii::uid::Sample>& sample,size_t count) {
      aii_voice_capture evidence{};
      if(sample) {
        evidence.samples=count;
        std::snprintf(evidence.embedding_binding,sizeof evidence.embedding_binding,"%s",binding.c_str());
        std::snprintf(evidence.pcm_sha256,sizeof evidence.pcm_sha256,"%s",sample->audio_sha256.c_str());
        std::copy(sample->embedding.begin(),sample->embedding.end(),evidence.embedding);
      }
      char output[8192]{};size_t written=0;
      const auto status=callback(context,session,utterance,sample?&evidence:nullptr,output,sizeof output,&written);
      if(status==AII_VOICE_BUSY)throw aii::voice::SpeakerStorageLate("the host's storage did not answer in time");
      if(status!=AII_VOICE_OK || !written || written>=sizeof output)
        throw aii::voice::EnrollmentUnavailable("speaker registry publication unavailable");
      return std::string(output,written);
    });
  }
  void track_observer(aii_voice_track_observer callback,void* context) override {
    if(!uid||!callback||!context)throw std::invalid_argument("native UID observer unavailable");
    const auto binding=uid_policies->current().policy.embedding_binding;
    uid->track_observer([callback,context,binding](const std::optional<aii::uid::Sample>& sample,size_t count) {
      aii_voice_capture evidence{};
      if(sample) {
        evidence.samples=count;
        std::snprintf(evidence.embedding_binding,sizeof evidence.embedding_binding,"%s",binding.c_str());
        std::snprintf(evidence.pcm_sha256,sizeof evidence.pcm_sha256,"%s",sample->audio_sha256.c_str());
        std::copy(sample->embedding.begin(),sample->embedding.end(),evidence.embedding);
      }
      char output[8192]{};size_t written=0;
      const auto status=callback(context,sample?&evidence:nullptr,output,sizeof output,&written);
      if(status==AII_VOICE_BUSY)throw aii::voice::SpeakerStorageLate("the host's storage did not answer in time");
      if(status!=AII_VOICE_OK || !written || written>=sizeof output)
        throw aii::voice::EnrollmentUnavailable("speaker registry publication unavailable");
      return std::string(output,written);
    });
  }
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
};
struct Owner final : SpeakerOwner {
  const bool supplied_recognizer;
  aii::voice::NativeModels models;
  std::string tts_backend;
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
#if defined(__linux__) && !defined(__ANDROID__)
    const auto tts=models.tts_execution_info();
#else
    const auto tts="{\"requested_backend\":\""+tts_backend+"\",\"hardware_execution_verified\":false}";
#endif
    return "{\"asr\":"+models.recognizer().execution_info()+
      ",\"tts\":"+tts+","+
      "\"vad\":{\"provider\":\""+detector+"\",\"hardware_execution_verified\":false},"+
      "\"endpoint\":{\"provider\":\""+detector+"\",\"hardware_execution_verified\":false},"+
      "\"uid\":{\"provider\":\""+(speaker()?std::string(aii::voice::uid_provider):"not_loaded")+"\",\"hardware_execution_verified\":false}}";
  }
  aii_voice_readiness warm() override {
    auto result=warm_parts(models.recognizer(),models.vad(),models.endpoint(),models.synthesizer(),speaker());
    if(supplied_recognizer){std::snprintf(result.accelerator,sizeof result.accelerator,"%s","external_recognizer");return result;}
    std::string uid_used="cpu";
#ifdef AII_WITH_UID
    if(uid)uid_used=std::string(aii::voice::uid_backend);
#endif
    aii::voice::put_accelerator(result.accelerator,
        aii::voice::registered_accelerators(tts_backend,models.recognizer().execution_info(),uid_used));
    return result;
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
namespace {
#ifdef AII_WITH_UID
aii::voice::NativeSpeaker::ReadSnapshot snapshot_reader(const aii::uid::BoundPolicies& policies,
    aii_voice_snapshot_reader reader,void* context) {
  return [policies,reader,context] {
    std::string bytes(8u<<20,'\0');size_t written=0;
    const auto status=reader(context,bytes.data(),bytes.size(),&written);
    if(status==AII_VOICE_AGAIN && !written)
      return aii::uid::Snapshot{policies.current().policy,0,{}};
    if(status==AII_VOICE_BUSY)throw aii::voice::SpeakerStorageLate("the host's storage did not answer in time");
    if(status!=AII_VOICE_OK||!written||written>bytes.size())
      throw std::runtime_error("authoritative UID snapshot unavailable");
    bytes.resize(written);return policies.read(bytes);
  };
}
std::optional<aii::uid::Policy> previous_of(const aii::uid::BoundPolicies& policies) {
  return policies.previous()?std::optional<aii::uid::Policy>(policies.previous()->policy):std::nullopt;
}
#endif
std::unique_ptr<Owner> build_owner(const aii_voice_paths* p,const char* backend,
 const char* uid_model,const char* policy_json,size_t policy_bytes,aii_voice_snapshot_reader reader,void* context,
 std::unique_ptr<aii::voice::Recognizer> supplied,const char* execution,
 const char* previous_policy,size_t previous_bytes) {
  if(!p)throw std::invalid_argument("paths and empty model handle required");
  if(!backend || (std::strcmp(backend,"cpu") && std::strcmp(backend,"vulkan") && std::strcmp(backend,"metal")))
    throw std::invalid_argument("explicit cpu, vulkan or metal TTS backend required; no fallback");
  if(!std::strcmp(backend,"vulkan")) {
    const auto fp32=environment("GGML_VK_DISABLE_F16");
#if defined(__linux__) && !defined(__ANDROID__)
    if(fp32!="1")
      throw std::invalid_argument("Vulkan requires explicit FP32 process binding");
#else
    const auto device=environment("GGML_VK_VISIBLE_DEVICES");
    if(fp32!="1" || device!="0")
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
    owner->uid=std::make_unique<aii::voice::NativeSpeaker>(uid_model,aii::voice::uid_backend.data(),policy.policy,
        snapshot_reader(policies,reader,context),previous_of(policies));
#else
    throw std::invalid_argument("runtime not linked with native UID");
#endif
  }
  return owner;
}
template <class F> aii_voice_result guarded(aii_voice_error* e,F f) {
  if(e)e->message[0]=0;
  try { return f(); }
  catch(const std::invalid_argument& ex) { if(e)std::snprintf(e->message,sizeof e->message,"%s",ex.what());return AII_VOICE_INVALID; }
  catch(const std::exception& ex) { if(e)std::snprintf(e->message,sizeof e->message,"%s",ex.what());return AII_VOICE_FAILED; }
  catch(...) { if(e)std::snprintf(e->message,sizeof e->message,"unknown native model load failure");return AII_VOICE_FAILED; }
}
}
aii_voice_result aii::voice::load_native_models(const aii_voice_paths* p,const char* backend,
 const char* uid_model,const char* policy_json,size_t policy_bytes,aii_voice_snapshot_reader reader,void* context,
 aii_voice_models** out,aii_voice_error* e,std::unique_ptr<Recognizer> supplied,const char* execution,
 const char* previous_policy,size_t previous_bytes) {
  return guarded(e,[&] {
    if(!p || !out || *out)throw std::invalid_argument("paths and empty model handle required");
    *out=aii::voice::wrap_models(build_owner(p,backend,uid_model,policy_json,policy_bytes,reader,context,
        std::move(supplied),execution,previous_policy,previous_bytes));
    return AII_VOICE_OK;
  });
}
extern "C" aii_voice_result aii_voice_models_load_uid_policies(const aii_voice_paths* p,const char* backend,
 const char* uid_model,const char* policy_json,size_t policy_bytes,aii_voice_snapshot_reader reader,void* context,
 const char* execution,const char* previous_policy,size_t previous_bytes,aii_voice_models** out,aii_voice_error* e) {
  return aii::voice::load_native_models(p,backend,uid_model,policy_json,policy_bytes,reader,context,out,e,nullptr,
      execution,previous_policy,previous_bytes);
}
namespace {
void close_end(aii::voice::ChannelEnd end) noexcept {
#ifdef _WIN32
  if(end.in>0)CloseHandle(reinterpret_cast<HANDLE>(end.in));
  if(end.out>0&&end.out!=end.in)CloseHandle(reinterpret_cast<HANDLE>(end.out));
#else
  if(end.in>=0)::close(int(end.in));
  if(end.out>=0&&end.out!=end.in)::close(int(end.out));
#endif
}
bool valid_end(aii::voice::ChannelEnd end) {
#ifdef _WIN32
  return end.in>0&&end.out>0&&end.in!=end.out;
#else
  return end.in>=0&&end.in<=INT32_MAX&&end.out>=0&&end.out<=INT32_MAX&&end.in!=end.out;
#endif
}
bool valid_control(aii::voice::ChannelEnd end) {
#ifdef _WIN32
  return valid_end(end);
#else
  return end.in>=0&&end.in<=INT32_MAX&&end.out==end.in;
#endif
}
struct ServerFactory final : aii::voice::StreamFactory {
  Owner& owner;
  std::string description;
  aii::voice::Admission gate;
  ServerFactory(Owner& o,size_t ceiling,std::chrono::milliseconds longest)
      :owner(o),gate(ceiling,longest,[]{return aii::voice::NativeModels::speech_lane_costs();}) {}
  aii::voice::Admission* admission() override { return &gate; }
  std::unique_ptr<aii::voice::Synthesizer> speech() override { return owner.models.open_speech(); }
  std::unique_ptr<aii::voice::Recognizer> recognizer() override { return owner.models.open_recognizer(); }
  std::unique_ptr<aii::voice::Endpoint> endpoint() override { return owner.models.open_endpoint(); }
  std::unique_ptr<aii::voice::SpeakerEmbedder> speaker() override {
#ifdef AII_WITH_UID
    if(owner.uid)return aii::voice::local_speaker_embedder(owner.uid->model());
#endif
    throw std::logic_error("model server: no speaker model");
  }
  std::string describe() override { return description; }
};
struct ChannelOwner final : SpeakerOwner {
  aii::voice::ModelChannel channel;
  std::string accelerator,info;
  uint32_t server_probe_ms{};
  std::unique_ptr<aii::voice::Recognizer> asr;
  std::unique_ptr<aii::voice::Endpoint> end;
  std::unique_ptr<aii::voice::Synthesizer> tts;
  std::unique_ptr<aii::voice::Vad> own_vad;
  ChannelOwner(aii::voice::ChannelEnd ends,const std::string& vad_path):channel(ends) {
    const auto described=channel.describe();
    const auto cut=described.find('\n');
    const auto second=cut==std::string::npos?cut:described.find('\n',cut+1);
    if(cut==std::string::npos||cut==0||cut>=sizeof(aii_voice_readiness::accelerator)||second==std::string::npos||
       second==cut+1||second-cut-1>10||described.find_first_not_of("0123456789",cut+1)!=second)
      throw std::runtime_error("model server description malformed");
    const auto probe=std::stoull(described.substr(cut+1,second-cut-1));
    if(probe<1||probe>UINT32_MAX)throw std::runtime_error("model server description malformed");
    accelerator=described.substr(0,cut);server_probe_ms=uint32_t(probe);info=described.substr(second+1);
    asr=channel.recognizer();end=channel.endpoint();tts=channel.speech();
    own_vad=aii::voice::NativeModels::load_vad(vad_path);
  }
  aii::voice::Recognizer& recognizer() override { return *asr; }
  aii::voice::Vad& vad() override { return *own_vad; }
  aii::voice::Endpoint& endpoint() override { return *end; }
  aii::voice::Synthesizer& synthesizer() override { return *tts; }
  std::string execution_info() override { return info; }
  bool lost() override { return channel.lost(); }
  aii::voice::Admission::Answer admit() override { return channel.admit(); }
  void release() override { channel.release(); }
  aii_voice_readiness warm() override {
    aii_voice_readiness result{};
    result.models_loaded=speaker()?5:4;result.probe_ms=server_probe_ms;
    std::snprintf(result.accelerator,sizeof result.accelerator,"%s",accelerator.c_str());
    return result;
  }
};
aii_voice_result no_enrollment(void*,char*,size_t,size_t*) { return AII_VOICE_FAILED; }
}
extern "C" aii_voice_result aii_voice_model_server_run(const aii_voice_paths* p,const char* backend,
 const char* uid_model,const char* policy_json,size_t policy_bytes,const char* execution,
 const char* previous_policy,size_t previous_bytes,uint32_t warm_limit_ms,uint32_t retire_ms,
 uint32_t max_sessions,uint32_t session_open_ms,
 aii_voice_model_server_ready ready,void* context,intptr_t control_in,intptr_t control_out,aii_voice_error* e) {
  return guarded(e,[&] {
    const aii::voice::ChannelEnd control{control_in,control_out};
    if(!ready||!valid_control(control)||!warm_limit_ms||!retire_ms||!session_open_ms||max_sessions>aii::voice::model_server_channel_limit)
      throw std::invalid_argument("readiness callback, control, warm, retire and session_open limits, and a ceiling within the channel bound required");
    auto owner=build_owner(p,backend,uid_model,policy_json,policy_bytes,uid_model?no_enrollment:nullptr,nullptr,
        nullptr,execution,previous_policy,previous_bytes);
    const auto warmed=owner->warm();
    if(warmed.probe_ms>warm_limit_ms)
      throw std::runtime_error("native warm inference took "+std::to_string(warmed.probe_ms)+" ms, more than the "+
          std::to_string(warm_limit_ms)+" ms the limits table gives it (warm_probe_ms)");
    aii::voice::NativeModels::forget_speech_lane_costs();
    {
      auto& t=owner->models.synthesizer();
      t.open();t.start(2,"Good morning. The garden is watered and the doors are locked.");
      while(!t.next().empty()) {}
      t.reset();
    }
    {
      const auto costs=aii::voice::NativeModels::speech_lane_costs();
      std::fprintf(stderr,"AII_VOICE_MODEL_SERVER {\"event\":\"lane_costs\",\"rtf\":%.4f,\"first_sound_ms\":%.1f}\n",costs.rtf,costs.first_sound_ms);
    }
    ServerFactory factory(*owner,max_sessions?max_sessions:aii::voice::model_server_channel_limit,std::chrono::milliseconds(session_open_ms));
    factory.description=std::string(warmed.accelerator)+"\n"+std::to_string(warmed.probe_ms)+"\n"+owner->execution_info();
    ready(context,&warmed);
    aii::voice::serve_model_server(factory,control,std::chrono::milliseconds(retire_ms),[](const std::string& what) {
      std::fprintf(stderr,"AII_VOICE_MODEL_SERVER {\"event\":\"hung_stream\",\"reason\":\"%s\"}\n",what.c_str());
      std::fflush(stderr);
      std::_Exit(78);
    });
    return AII_VOICE_OK;
  });
}
extern "C" aii_voice_result aii_voice_models_load_channel(intptr_t channel_in,intptr_t channel_out,const char* vad_path,
 const char* policy_json,size_t policy_bytes,aii_voice_snapshot_reader reader,void* context,
 const char* previous_policy,size_t previous_bytes,aii_voice_models** out,aii_voice_error* e) {
  return guarded(e,[&] {
    const aii::voice::ChannelEnd channel{channel_in,channel_out};
    const auto refuse=[channel](const char* why) { close_end(channel);return std::invalid_argument(why); };
    if(!valid_end(channel))throw std::invalid_argument("channel ends required: a pipe each way");
    if(!vad_path||!*vad_path||!out||*out)throw refuse("channel, VAD path and empty model handle required");
    if((previous_policy==nullptr)!=(previous_bytes==0)||previous_bytes>4096)
      throw refuse("bounded previous UID policy required");
    if(!policy_json&&(policy_bytes||reader||context||previous_policy))throw refuse("partial UID configuration");
    auto owner=std::make_unique<ChannelOwner>(channel,vad_path);
    if(policy_json) {
#ifdef AII_WITH_UID
      if(!policy_bytes||policy_bytes>4096||!reader)throw std::invalid_argument("bound UID policy and snapshot reader required");
      const aii::uid::BoundPolicies policies(std::string(policy_json,policy_bytes),
          previous_policy?std::string(previous_policy,previous_bytes):std::string{});
      owner->uid_policies=policies;
      owner->uid=std::make_unique<aii::voice::NativeSpeaker>(owner->channel.speaker(),policies.current().policy,
          snapshot_reader(policies,reader,context),previous_of(policies));
#else
      throw std::invalid_argument("runtime not linked with native UID");
#endif
    }
    *out=aii::voice::wrap_models(std::move(owner));
    return AII_VOICE_OK;
  });
}
