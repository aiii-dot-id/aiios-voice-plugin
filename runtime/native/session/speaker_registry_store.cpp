#include "speaker_registry_store.h"
#include "../vendor/picosha2/picosha2.h"
#include <algorithm>
#include <cstring>
#ifdef _WIN32
#include <windows.h>
#include <bcrypt.h>
#else
#include <unistd.h>
#include <sys/random.h>
#endif

namespace aii::voice {
using namespace wire;
namespace {
std::string random_uuid() {
  unsigned char bytes[16];
#ifdef _WIN32
  require(BCryptGenRandom(nullptr,bytes,sizeof bytes,BCRYPT_USE_SYSTEM_PREFERRED_RNG)>=0,"speaker UUID entropy unavailable");
#else
  require(getentropy(bytes,sizeof bytes)==0,"speaker UUID entropy unavailable");
#endif
  bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
  const char* digits="0123456789abcdef";std::string out;
  for(size_t i=0;i<sizeof bytes;++i){if(i==4||i==6||i==8||i==10)out+='-';out+=digits[bytes[i]>>4];out+=digits[bytes[i]&15];}
  return out;
}
Json projection(const aii::uid::SpeakerRegistry& registry) {
  auto out=object(),rows=own(cJSON_CreateArray());
  put(out,"registry_revision",string(std::to_string(registry.revision)));
  for(const auto& bucket:registry.buckets) {
    auto row=object();put(row,"speaker_uuid",string(bucket.uuid));
    const bool profile=std::any_of(registry.profiles.speakers.begin(),registry.profiles.speakers.end(),[&](const auto& p){return p.id==bucket.uuid;});
    put(row,"profile_available",boolean(profile));
    if(!bucket.associations.empty()) {
      put(row,"display_label",string(bucket.associations.back().label));
      put(row,"external_id",string(bucket.associations.back().external_id));
    }
    require(cJSON_AddItemToArray(rows.get(),row.get()),"speaker list allocation");row.release();
  }
  put(out,"speakers",std::move(rows));put(out,"used_for_permissions",boolean(false));return out;
}
}
std::string SpeakerRegistryStore::read(bool& absent) {
  auto raw=bridge_.read(&absent,SnapshotBridge::Store::SpeakerRegistry);
  return absent?aii::uid::write_registry({0,{policy_.policy,0,{}},{}},policy_):raw;
}
void SpeakerRegistryStore::publish(const std::string& base,const aii::uid::RegistryChange& change,bool absent) {
  if(!absent && base==change.document)return;
  auto receipt=bridge_.publish(change.document,absent?"":change.base_sha256,absent,
      picosha2::hash256_hex_string(random_uuid()),SnapshotBridge::Store::SpeakerRegistry);
  require(flag(field(receipt.get(),"durable"))&&flag(field(receipt.get(),"readback_verified")),
      "speaker registry durability unresolved");
}
Json SpeakerRegistryStore::observe(const aii_voice_capture* evidence) {
  std::unique_lock<std::mutex> lock(mutex_,std::try_to_lock);
  require(lock.owns_lock(),"speaker registry busy");
  std::optional<aii::uid::Sample> sample;
  if(evidence) {
    require(evidence->samples>=31920 && evidence->samples<=160000 && evidence->embedding_binding[64]==0 &&
      std::string(evidence->embedding_binding)==policy_.policy.embedding_binding && evidence->pcm_sha256[64]==0,
      "speaker evidence binding differs");
    aii::uid::Vector vector{};std::copy(std::begin(evidence->embedding),std::end(evidence->embedding),vector.begin());
    sample=aii::uid::Sample{evidence->pcm_sha256,vector};
  }
  bool absent=false;const auto raw=read(absent);
  const auto registry=aii::uid::read_registry(raw,policy_);
  const auto change=aii::uid::observe_speaker(raw,policy_,registry.revision,random_uuid(),sample);
  publish(raw,change,absent);
  auto out=object();put(out,"outcome",string("unavailable"));put(out,"reason",string(change.reason));
  put(out,"speaker_uuid",string(change.uuid));put(out,"registry_revision",string(std::to_string(change.revision)));
  put(out,"continuity",string(change.continuity));put(out,"used_for_permissions",boolean(false));
  const auto next=aii::uid::read_registry(change.document,policy_);
  for(const auto& b:next.buckets)if(b.uuid==change.uuid && !b.associations.empty())
    put(out,"display_label",string(b.associations.back().label));
  return out;
}
Json SpeakerRegistryStore::list() {
  std::unique_lock<std::mutex> lock(mutex_,std::try_to_lock);require(lock.owns_lock(),"speaker registry busy");
  bool absent=false;return projection(aii::uid::read_registry(read(absent),policy_));
}
Json SpeakerRegistryStore::associate(uint64_t revision,const std::string& uuid,const std::string& label,const std::string& id) {
  std::unique_lock<std::mutex> lock(mutex_,std::try_to_lock);require(lock.owns_lock(),"speaker registry busy");
  bool absent=false;const auto raw=read(absent);
  const auto change=aii::uid::associate_speaker(raw,policy_,revision,uuid,label,id);
  publish(raw,change,absent);
  return projection(aii::uid::read_registry(change.document,policy_));
}
Json SpeakerRegistryStore::forget(uint64_t revision,const std::string& uuid) {
  std::unique_lock<std::mutex> lock(mutex_,std::try_to_lock);require(lock.owns_lock(),"speaker registry busy");
  bool absent=false;const auto raw=read(absent);
  const auto change=aii::uid::forget_speaker(raw,policy_,revision,uuid);
  publish(raw,change,absent);
  return projection(aii::uid::read_registry(change.document,policy_));
}
aii_voice_result SpeakerRegistryStore::callback(void* context,const aii_voice_capture* evidence,char* output,size_t capacity,size_t* written) noexcept {
  try {
    if(!context||!output||!written||capacity<8192)return AII_VOICE_INVALID;
    *written=0;const auto result=encode(static_cast<SpeakerRegistryStore*>(context)->observe(evidence));
    if(result.empty()||result.size()>=capacity)return AII_VOICE_CAPACITY;
    std::memcpy(output,result.data(),result.size());*written=result.size();return AII_VOICE_OK;
  }catch(...){return AII_VOICE_FAILED;}
}
}
