#include "native_speaker.h"
#include "startup_trace.h"
#include "worker_json.h"
#include "../../native_uid/uid.h"
#include "../../native_uid/model_contract.h"
#include "../../native_uid/session_evidence.h"
#include "../vendor/picosha2/picosha2.h"
#include "../platform/readonly_model.h"
#include <algorithm>
#include <atomic>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <limits>

namespace aii::voice {
namespace {
std::string decision_json(const aii::uid::Decision& d,const aii::uid::Sample& sample,
                          size_t count,const aii::uid::Policy& policy) {
  using namespace wire;
  auto j=object();
  put(j,"outcome",string(d.outcome));put(j,"reason",string(d.reason));
  put(j,"speaker_id",d.speaker_id.empty()?null():string(d.speaker_id));
  put(j,"label",d.label.empty()?null():string(d.label));
  put(j,"score",d.score?own(cJSON_CreateNumber(*d.score)):null());
  put(j,"margin",d.margin?own(cJSON_CreateNumber(*d.margin)):null());
  put(j,"enrollment_revision",string(std::to_string(d.enrollment_revision)));
  put(j,"policy_sha256",string(d.policy_sha256));
  put(j,"embedding_binding",string(policy.embedding_binding));
  put(j,"pcm_sha256",string(sample.audio_sha256));
  put(j,"samples",number(count));put(j,"used_for_permissions",boolean(false));
  put(j,"qualification",string("development_only"));
  return encode(j);
}
}
struct SpeakerModel {
  AiiUid* handle=nullptr;
  std::mutex lane;
  uint64_t next=0;
  ~SpeakerModel(){aii_uid_destroy(handle);}
};
std::shared_ptr<SpeakerModel> load_speaker_model(const std::string& path,const std::string& backend,
    const std::string& embedding_binding) {
  StartupSpan profile("load_uid");
  auto model=std::make_shared<SpeakerModel>();
  const auto& contract=aii::uid::model_contract(embedding_binding);
#ifdef AII_UID_NCNN
  if(backend=="ncnn-cpu"||backend=="ncnn-vulkan") {
    auto read_bound=[&](const char* name,size_t size) {
      std::ifstream f(std::filesystem::u8path(path)/name,std::ios::binary|std::ios::ate);
      if(!f||f.tellg()!=std::streamoff(size))throw std::invalid_argument("native UID representation size differs");
      std::vector<unsigned char> b(size);f.seekg(0);f.read(reinterpret_cast<char*>(b.data()),b.size());
      if(!f)throw std::invalid_argument("native UID representation short read");
      return b;
    };
    const auto& n=aii::uid::ncnn_contract;
    auto graph=read_bound("model.ncnn.param",n.graph_bytes);
    auto weights=read_bound("model.ncnn.bin",n.weight_bytes);
    aii::uid::verify_ncnn(contract,graph.size(),aii::platform::sha256(graph.data(),graph.size()),
                        weights.size(),aii::platform::sha256(weights.data(),weights.size()));
    char error[1024]={};
    model->handle=aii_uid_create_ncnn(graph.data(),graph.size(),weights.data(),weights.size(),backend.c_str(),error,sizeof(error));
    if(!model->handle)throw std::runtime_error(error);
    return model;
  }
#endif
  std::ifstream file(std::filesystem::u8path(path),std::ios::binary|std::ios::ate);
  if(!file||file.tellg()!=std::streamoff(contract.bytes))throw std::invalid_argument("bound UID model size differs");
  std::vector<unsigned char> bytes(contract.bytes);file.seekg(0);
  file.read(reinterpret_cast<char*>(bytes.data()),bytes.size());
  if(!file)throw std::invalid_argument("bound UID model short read");
  aii::uid::verify_model(contract,bytes.size(),aii::platform::sha256(bytes.data(),bytes.size()));
  char error[1024]={};model->handle=aii_uid_create(bytes.data(),bytes.size(),backend.c_str(),error,sizeof(error));
  if(!model->handle)throw std::runtime_error(error);
  return model;
}
namespace {
struct LocalEmbedder final:SpeakerEmbedder {
  std::shared_ptr<SpeakerModel> model;
  std::atomic<bool> cancelled{false};
  std::atomic<uint64_t> active{0};
  explicit LocalEmbedder(std::shared_ptr<SpeakerModel> m):model(std::move(m)) {
    if(!model||!model->handle)throw std::invalid_argument("loaded speaker model required");
  }
  aii::uid::Vector embed(const std::vector<uint8_t>& pcm,size_t dimensions) override {
    std::lock_guard<std::mutex> lane(model->lane);
    if(model->next==std::numeric_limits<uint64_t>::max())throw std::runtime_error("UID generation exhausted");
    const auto id=++model->next;active=id;
    if(cancelled) {active=0;throw Cancelled("UID cancelled");}
    aii::uid::Vector embedding(dimensions);char error[1024]={};
    const auto rc=aii_uid_embed(model->handle,id,pcm.data(),pcm.size(),16000,embedding.data(),embedding.size(),error,sizeof(error));
    active=0;
    if(rc==3)throw Cancelled("UID cancelled");
    if(rc)throw std::runtime_error(error);
    return embedding;
  }
  void cancel() noexcept override {
    cancelled=true;
    const auto id=active.load();
    if(id)(void)aii_uid_cancel_through(model->handle,id);
  }
  void resume() override {cancelled=false;}
};
}
std::unique_ptr<SpeakerEmbedder> local_speaker_embedder(std::shared_ptr<SpeakerModel> model) {
  return std::make_unique<LocalEmbedder>(std::move(model));
}
struct NativeSpeaker::Impl {
  std::shared_ptr<SpeakerModel> model;
  aii::uid::Policy policy;
  std::optional<aii::uid::Policy> previous;
  ReadSnapshot read;
  ObserveTrack observe;
  ObserveTrackAt observe_at;
  uint64_t session_epoch=0;
  std::unique_ptr<SpeakerEmbedder> embedder;
  std::atomic<bool> cancelled{false};
  aii::uid::SessionEvidence evidence;
  Impl(aii::uid::Policy p,ReadSnapshot r,std::optional<aii::uid::Policy> old)
      :policy(std::move(p)),previous(std::move(old)),read(std::move(r)) {}
  const aii::uid::Policy& expected(const aii::uid::Snapshot& snapshot) const {
    const auto& selected=previous&&snapshot.policy.fingerprint==previous->fingerprint?*previous:policy;
    aii::uid::validate(snapshot,selected);return selected;
  }
  void validate() const {
    if(!read)throw std::invalid_argument("authoritative UID snapshot reader required");
    aii::uid::validate({policy,0,{}},policy);
    if(previous) {
      const auto& old=*previous;aii::uid::validate({old,0,{}},old);
      if(old.minimum_enrollment_samples!=3||policy.minimum_enrollment_samples!=1||
          old.embedding_binding!=policy.embedding_binding||old.threshold!=policy.threshold||
          old.minimum_margin!=policy.minimum_margin||old.calibration_sha256==policy.calibration_sha256)
        throw std::invalid_argument("previous UID policy is not a compatible explicit transition");
    }
  }
};
NativeSpeaker::NativeSpeaker(std::shared_ptr<SpeakerModel> model,aii::uid::Policy policy,ReadSnapshot read,
    std::optional<aii::uid::Policy> previous)
    :p_(std::make_unique<Impl>(std::move(policy),std::move(read),std::move(previous))) {
  if(!model||!model->handle)throw std::invalid_argument("loaded speaker model required");
  p_->validate();p_->model=std::move(model);p_->embedder=local_speaker_embedder(p_->model);
}
NativeSpeaker::NativeSpeaker(std::unique_ptr<SpeakerEmbedder> embedder,aii::uid::Policy policy,ReadSnapshot read,
    std::optional<aii::uid::Policy> previous)
    :p_(std::make_unique<Impl>(std::move(policy),std::move(read),std::move(previous))) {
  if(!embedder)throw std::invalid_argument("speaker embedder required");
  p_->validate();p_->embedder=std::move(embedder);
}
std::shared_ptr<SpeakerModel> NativeSpeaker::model() const {return p_->model;}
NativeSpeaker::NativeSpeaker(const std::string& path,const std::string& backend,aii::uid::Policy policy,ReadSnapshot read,
    std::optional<aii::uid::Policy> previous)
    :p_(std::make_unique<Impl>(std::move(policy),std::move(read),std::move(previous))) {
  p_->validate();
  p_->model=load_speaker_model(path,backend,p_->policy.embedding_binding);
  p_->embedder=local_speaker_embedder(p_->model);
}
NativeSpeaker::~NativeSpeaker()=default;
void NativeSpeaker::track_observer(ObserveTrack observer) {
  if(!observer)throw std::invalid_argument("track observer required");
  p_->observe=std::move(observer);
}
void NativeSpeaker::track_observer_at(ObserveTrackAt observer) {
  if(!observer)throw std::invalid_argument("track observer required");
  p_->observe_at=std::move(observer);
}
std::string NativeSpeaker::identify_track_at(uint64_t final,uint64_t utterance,const std::vector<float>& pcm) {
  if(!p_->observe_at)return identify_track(final,pcm);
  if(p_->cancelled)throw Cancelled("UID cancelled");
  const auto epoch=p_->evidence.epoch();
  std::optional<aii::uid::Sample> sample;
  if(!pcm.empty())sample=recording(pcm);
  if(p_->cancelled)throw Cancelled("UID cancelled");
  if(sample)p_->evidence.retain(epoch,final,*sample);
  if(model_part_done)model_part_done();
  const auto result=p_->observe_at(p_->session_epoch,utterance,sample,pcm.size());
  if(p_->cancelled)throw Cancelled("UID cancelled");
  return result;
}
std::string NativeSpeaker::identify_track(uint64_t final,const std::vector<float>& pcm) {
  if(!p_->observe)throw EnrollmentUnavailable("speaker registry unavailable");
  if(p_->cancelled)throw Cancelled("UID cancelled");
  const auto epoch=p_->evidence.epoch();
  std::optional<aii::uid::Sample> sample;
  if(!pcm.empty())sample=recording(pcm);
  if(p_->cancelled)throw Cancelled("UID cancelled");
  if(sample)p_->evidence.retain(epoch,final,*sample);
  if(model_part_done)model_part_done();
  const auto result=p_->observe(sample,pcm.size());
  if(p_->cancelled)throw Cancelled("UID cancelled");
  return result;
}
void NativeSpeaker::warm() {
  std::vector<uint8_t> pcm(64000);
  for(size_t i=0;i<pcm.size()/2;++i) {
    const int16_t value=int16_t(4096*(std::sin(.017*double(i))+std::sin(.039*double(i))));
    pcm[2*i]=uint8_t(uint16_t(value)&255);pcm[2*i+1]=uint8_t(uint16_t(value)>>8);
  }
  const bool fenced=p_->cancelled;
  if(fenced)p_->embedder->resume();
  aii::uid::Vector v;
  try {v=p_->embedder->embed(pcm,aii::uid::embedding_dimensions(p_->policy.embedding_binding));}
  catch(...) {if(fenced)p_->embedder->cancel();throw;}
  if(fenced)p_->embedder->cancel();
  double norm=0;for(double x:v){if(!std::isfinite(x))throw std::runtime_error("nonfinite warm UID");norm+=x*x;}
  if(std::abs(norm-1)>1e-6)throw std::runtime_error("invalid warm UID norm");
}
void NativeSpeaker::cancel_fence() noexcept {p_->cancelled=true;p_->embedder->cancel();}
void NativeSpeaker::open() {
  if(p_->session_epoch==UINT64_MAX)throw std::runtime_error("speaker session epoch exhausted");
  ++p_->session_epoch;p_->evidence.begin();p_->cancelled=false;p_->embedder->resume();
}
void NativeSpeaker::cancel() noexcept {
  p_->cancelled=true;
  p_->evidence.cancel();
  p_->embedder->cancel();
}
aii::uid::Sample NativeSpeaker::recording(const std::vector<float>& pcm) {
  if(p_->cancelled)throw Cancelled("UID cancelled");
  if(pcm.size()<31920||pcm.size()>480000)throw std::invalid_argument("complete UID context required");
  std::vector<uint8_t> bytes(pcm.size()*2);
  for(size_t i=0;i<pcm.size();++i) {
    if(!std::isfinite(pcm[i])||pcm[i]<-1||pcm[i]>1)throw std::invalid_argument("UID PCM range differs");
    const auto n=static_cast<int>(std::nearbyint(std::clamp(double(pcm[i])*32768.,-32768.,32767.)));
    bytes[2*i]=uint8_t(n&255);bytes[2*i+1]=uint8_t((uint32_t(n)>>8)&255);
  }
  auto embedding=p_->embedder->embed(bytes,aii::uid::embedding_dimensions(p_->policy.embedding_binding));
  if(p_->cancelled)throw Cancelled("UID cancelled");
  return {picosha2::hash256_hex_string(bytes),std::move(embedding)};
}
aii::uid::PreparedEnrollment NativeSpeaker::prepare_enrollment(const std::string& current,
    const aii::uid::PolicyDocument& policy,const std::string& speaker_id,
    const std::string& label,const std::vector<std::vector<float>>& recordings) {
  p_->expected(aii::uid::read_snapshot(current,policy));
  if(recordings.empty()||recordings.size()>8)throw std::invalid_argument("bounded enrollment recordings required");
  std::vector<aii::uid::Sample> samples;samples.reserve(recordings.size());
  for(const auto& pcm:recordings)samples.push_back(recording(pcm));
  if(p_->cancelled)throw Cancelled("UID cancelled");
  return aii::uid::prepare_enrollment(current,policy,speaker_id,label,samples,p_->policy.embedding_binding);
}
aii::uid::Sample NativeSpeaker::prepare_capture(const std::vector<float>& pcm) {
  p_->cancelled=false;p_->embedder->resume();
  try {auto sample=recording(pcm);cancel_fence();return sample;}
  catch(...) {cancel_fence();throw;}
}
std::vector<uint64_t> NativeSpeaker::enrollment_finals() {return p_->evidence.available();}
aii::uid::PreparedEnrollment NativeSpeaker::prepare_selected(const std::string& current,
    const aii::uid::PolicyDocument& policy,const std::string& speaker_id,
    const std::string& label,const std::vector<uint64_t>& finals) {
  const auto epoch=p_->evidence.epoch();
  p_->expected(aii::uid::read_snapshot(current,policy));
  const auto samples=p_->evidence.select(epoch,finals);
  auto candidate=aii::uid::prepare_enrollment(current,policy,speaker_id,label,samples,p_->policy.embedding_binding);
  if(p_->cancelled)throw Cancelled("UID cancelled");
  (void)p_->evidence.select(epoch,finals);
  return candidate;
}
std::string NativeSpeaker::identify(uint64_t final,const std::vector<float>& pcm) {
  const auto epoch=p_->evidence.epoch();
  const auto sample=recording(pcm);
  p_->evidence.retain(epoch,final,sample);
  if(model_part_done)model_part_done();
  const auto snapshot=[&] {
    try{return p_->read();}
    catch(const Cancelled&){throw;}
    catch(const SpeakerStorageLate&){throw;}
    catch(const std::exception&){throw EnrollmentUnavailable("authoritative enrollment unavailable");}
  }();
  if(p_->cancelled)throw Cancelled("UID cancelled");
  const auto d=aii::uid::identify(snapshot,p_->expected(snapshot),sample.embedding,p_->policy.embedding_binding);
  return decision_json(d,sample,pcm.size(),p_->policy);
}
}
