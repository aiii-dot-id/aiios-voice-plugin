#include "native_speaker.h"
#include "startup_trace.h"
#include "worker_json.h"
#include "../../native_uid/uid.h"
#include "../../native_uid/session_evidence.h"
#include "../vendor/picosha2/picosha2.h"
#include "../platform/readonly_model.h"
#include <algorithm>
#include <atomic>
#include <cmath>
#include <fstream>
#include <limits>

namespace aii::voice {
struct NativeSpeaker::Impl {
  AiiUid* model=nullptr;
  aii::uid::Policy policy;
  std::optional<aii::uid::Policy> previous;
  ReadSnapshot read;
  std::atomic<bool> cancelled{false};
  std::atomic<uint64_t> active{0};
  uint64_t next=0; // one inference caller, monotonic across session reuse
  aii::uid::SessionEvidence evidence;
  Impl(aii::uid::Policy p,ReadSnapshot r,std::optional<aii::uid::Policy> old)
      :policy(std::move(p)),previous(std::move(old)),read(std::move(r)) {}
  const aii::uid::Policy& expected(const aii::uid::Snapshot& snapshot) const {
    const auto& selected=previous&&snapshot.policy.fingerprint==previous->fingerprint?*previous:policy;
    aii::uid::validate(snapshot,selected);return selected;
  }
  ~Impl(){aii_uid_destroy(model);}
};
NativeSpeaker::NativeSpeaker(const std::string& path,const std::string& backend,aii::uid::Policy policy,ReadSnapshot read,
    std::optional<aii::uid::Policy> previous)
    :p_(std::make_unique<Impl>(std::move(policy),std::move(read),std::move(previous))) {
  StartupSpan profile("load_uid");
  if(!p_->read)throw std::invalid_argument("authoritative UID snapshot reader required");
  aii::uid::validate({p_->policy,0,{}},p_->policy);
  if(p_->previous) {
    const auto& old=*p_->previous;aii::uid::validate({old,0,{}},old);
    if(old.minimum_enrollment_samples!=3||p_->policy.minimum_enrollment_samples!=1||
        old.embedding_binding!=p_->policy.embedding_binding||old.threshold!=p_->policy.threshold||
        old.minimum_margin!=p_->policy.minimum_margin||old.calibration_sha256==p_->policy.calibration_sha256)
      throw std::invalid_argument("previous UID policy is not a compatible explicit transition");
  }
  if(p_->policy.embedding_binding!="c61bbdf12d5b69632b12776c23edc2e7155a9e0a028576eb41e509ca2bfb6d6e")
    throw std::invalid_argument("policy does not bind this native full-context UID frontend/model");
  std::ifstream file(path,std::ios::binary|std::ios::ate);
  if(!file||file.tellg()!=100865597)throw std::invalid_argument("bound UID model size differs");
  std::vector<unsigned char> bytes(100865597);file.seekg(0);
  file.read(reinterpret_cast<char*>(bytes.data()),bytes.size());
  // Keep full integrity verification; use the same OS-accelerated SHA256
  // implementation as recognition instead of a second portable hot path.
  if(!file||aii::platform::sha256(bytes.data(),bytes.size())!=
      "33af8affe6191b1ebd196d2b56e22c2934104cd2764abfdbdd954d3a934eb2a1")
    throw std::invalid_argument("bound UID model hash differs");
  char error[1024]={};p_->model=aii_uid_create(bytes.data(),bytes.size(),backend.c_str(),error,sizeof(error));
  if(!p_->model)throw std::runtime_error(error);
}
NativeSpeaker::~NativeSpeaker()=default;
void NativeSpeaker::warm() {
  if(p_->next==UINT64_MAX)throw std::runtime_error("UID generation exhausted");
  // The real frontend correctly refuses silence. A deterministic non-silent
  // warm signal exercises inference, without pretending to enroll a speaker.
  std::vector<uint8_t> pcm(64000);aii::uid::Vector v{};char error[1024]={};
  for(size_t i=0;i<pcm.size()/2;++i) {
    const int16_t value=int16_t(4096*(std::sin(.017*double(i))+std::sin(.039*double(i))));
    pcm[2*i]=uint8_t(uint16_t(value)&255);pcm[2*i+1]=uint8_t(uint16_t(value)>>8);
  }
  const auto rc=aii_uid_embed(p_->model,++p_->next,pcm.data(),pcm.size(),16000,v.data(),v.size(),error,sizeof error);
  if(rc)throw std::runtime_error(error);
  double norm=0;for(double x:v){if(!std::isfinite(x))throw std::runtime_error("nonfinite warm UID");norm+=x*x;}
  if(std::abs(norm-1)>1e-6)throw std::runtime_error("invalid warm UID norm");
}
void NativeSpeaker::open() {p_->evidence.begin();p_->cancelled=false;}
void NativeSpeaker::cancel() noexcept {
  p_->cancelled=true;
  p_->evidence.cancel();
  const auto id=p_->active.load();
  if(id)(void)aii_uid_cancel_through(p_->model,id);
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
  if(p_->next==std::numeric_limits<uint64_t>::max())throw std::runtime_error("UID generation exhausted");
  const auto id=++p_->next;p_->active=id;
  // Covers cancel just before publication of active, without locking control
  // behind inference. Cancel after this check fences this exact model call.
  if(p_->cancelled) {p_->active=0;throw Cancelled("UID cancelled");}
  aii::uid::Vector embedding{};char error[1024]={};
  const auto rc=aii_uid_embed(p_->model,id,bytes.data(),bytes.size(),16000,
                            embedding.data(),embedding.size(),error,sizeof(error));
  p_->active=0;
  if(rc==3)throw Cancelled("UID cancelled");
  if(rc)throw std::runtime_error(error);
  if(p_->cancelled)throw Cancelled("UID cancelled");
  return {picosha2::hash256_hex_string(bytes),embedding};
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
  // ModelOwner's exclusive lease excludes live identification and preparation.
  // Do not call open(): this is not a speech session or new ambient evidence.
  p_->cancelled=false;
  try {auto sample=recording(pcm);p_->cancelled=true;return sample;}
  catch(...) {p_->cancelled=true;throw;}
}
std::vector<uint64_t> NativeSpeaker::enrollment_finals() {return p_->evidence.available();}
aii::uid::PreparedEnrollment NativeSpeaker::prepare_selected(const std::string& current,
    const aii::uid::PolicyDocument& policy,const std::string& speaker_id,
    const std::string& label,const std::vector<uint64_t>& finals) {
  const auto epoch=p_->evidence.epoch();
  p_->expected(aii::uid::read_snapshot(current,policy));
  const auto samples=p_->evidence.select(epoch,finals);
  auto candidate=aii::uid::prepare_enrollment(current,policy,speaker_id,label,samples,p_->policy.embedding_binding);
  // Preparation never publishes. The composition root must still observe its
  // session/act fence immediately before asking the host to publish.
  if(p_->cancelled)throw Cancelled("UID cancelled");
  (void)p_->evidence.select(epoch,finals);
  return candidate;
}
std::string NativeSpeaker::identify(uint64_t final,const std::vector<float>& pcm) {
  const auto epoch=p_->evidence.epoch();
  const auto sample=recording(pcm);
  // Missing enrollment must not lose the recording needed to create the first
  // profile. No file write occurs and abort/new-open retire these vectors.
  p_->evidence.retain(epoch,final,sample);
  const auto snapshot=[&] {
    try{return p_->read();}
    catch(const Cancelled&){throw;}
    catch(const std::exception&){throw EnrollmentUnavailable("authoritative enrollment unavailable");}
  }();
  if(p_->cancelled)throw Cancelled("UID cancelled");
  const auto d=aii::uid::identify(snapshot,p_->expected(snapshot),sample.embedding,p_->policy.embedding_binding);
  using namespace wire;
  auto j=object();
  put(j,"outcome",string(d.outcome));put(j,"reason",string(d.reason));
  put(j,"speaker_id",d.speaker_id.empty()?null():string(d.speaker_id));
  put(j,"label",d.label.empty()?null():string(d.label));
  put(j,"score",d.score?own(cJSON_CreateNumber(*d.score)):null());
  put(j,"margin",d.margin?own(cJSON_CreateNumber(*d.margin)):null());
  // A string preserves the existing full signed-64-bit enrollment revision.
  put(j,"enrollment_revision",string(std::to_string(d.enrollment_revision)));
  put(j,"policy_sha256",string(d.policy_sha256));
  put(j,"embedding_binding",string(p_->policy.embedding_binding));
  put(j,"pcm_sha256",string(sample.audio_sha256));
  put(j,"samples",number(pcm.size()));put(j,"used_for_permissions",boolean(false));
  put(j,"qualification",string("development_only"));
  return encode(j);
}
}
