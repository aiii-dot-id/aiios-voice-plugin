#include "uid_recovery.h"
#include "../../native_uid/speaker_registry.h"
#include "../vendor/picosha2/picosha2.h"

namespace aii::voice {
using namespace wire;
namespace {
std::string hash(const std::string& raw){return picosha2::hash256_hex_string(raw);}
bool durable(const Json& receipt){return flag(field(receipt.get(),"durable"))&&flag(field(receipt.get(),"readback_verified"));}
void expected(const std::string& value){require(value=="absent"||(value.size()==64&&value.find_first_not_of("0123456789abcdef")==std::string::npos),"explicit observed UID digest or absent required");}
}
std::string UIDInspection::profile_hash() const{return profile_absent?"absent":hash(profile);}
std::string UIDInspection::captures_hash() const{return captures_absent?"absent":hash(captures);}
std::string UIDInspection::registry_hash() const{return registry_absent?"absent":hash(registry);}
bool UIDInspection::registry_recovery() const{return registry_state!="ready"&&registry_state!="absent";}
bool UIDInspection::needs_recovery() const {
  return (profile_state!="ready"&&profile_state!="absent")||(captures_state!="ready"&&captures_state!="absent")||registry_recovery();
}
Json UIDInspection::report() const {
  auto out=object();put(out,"enrollment_state",string(profile_state));put(out,"captures_state",string(captures_state));
  put(out,"required",boolean(needs_recovery()));
  put(out,"enrollment_sha256",string(profile_hash()));put(out,"captures_sha256",string(captures_hash()));
  if(!profile_model.empty())put(out,"stored_embedding_binding",string(profile_model));
  if(registry_recovery()){
    put(out,"speaker_registry_state",string(registry_state));put(out,"speaker_registry_sha256",string(registry_hash()));
    if(!registry_model.empty())put(out,"speaker_registry_stored_embedding_binding",string(registry_model));
  }
  return out;
}
UIDInspection inspect_uid(SnapshotBridge& bridge,const aii::uid::BoundPolicies& policies){
  UIDInspection state;
  // Storage errors propagate. Only typed FS_NOT_FOUND means absent.
  state.profile=bridge.read(&state.profile_absent);
  state.captures=bridge.read(&state.captures_absent,SnapshotBridge::Store::PendingCaptures);
  state.registry=bridge.read(&state.registry_absent,SnapshotBridge::Store::SpeakerRegistry);
  state.profile_state=state.profile_absent?"absent":"corrupt";
  if(!state.profile_absent){
    try{
      auto document=parse(state.profile);auto old=aii::uid::read_policy(encode(clone(field(document.get(),"policy"))));
      (void)aii::uid::read_snapshot(state.profile,old); // format only, NOT a matching policy
      state.profile_model=old.policy.embedding_binding;
      state.profile_state="incompatible";
      (void)policies.read(state.profile);state.profile_state="ready";
    }catch(const std::invalid_argument&){}catch(const Refused&){}
  }
  state.captures_state=state.captures_absent?"absent":"corrupt";
  if(!state.captures_absent){
    try{
      auto document=parse(state.captures);const auto* rows=field(document.get(),"captures");
      auto binding=policies.current().policy.embedding_binding;
      if(cJSON_IsArray(rows)&&rows->child)binding=str(field(field(rows->child,"recording"),"embedding_binding"),64);
      (void)aii::uid::read_captures(state.captures,binding);
      state.captures_state=binding==policies.current().policy.embedding_binding?"ready":"incompatible";
    }catch(const std::invalid_argument&){}catch(const Refused&){}
  }
  state.registry_state=state.registry_absent?"absent":"corrupt";
  if(!state.registry_absent){
    try{
      // The anonymous registry nests its profile snapshot, which names its policy.
      auto document=parse(state.registry);auto profiles=parse(str(field(document.get(),"profile_document"),8u<<20));
      auto old=aii::uid::read_policy(encode(clone(field(profiles.get(),"policy"))));
      (void)aii::uid::read_registry(state.registry,old); // format only, NOT a matching policy
      state.registry_model=old.policy.embedding_binding;
      state.registry_state="incompatible";
      // Same bound set as SpeakerRegistryStore: current, or a readable predecessor.
      const auto& previous=policies.previous();
      if(old.policy.fingerprint==policies.current().policy.fingerprint||
          (previous&&old.policy.fingerprint==previous->policy.fingerprint))state.registry_state="ready";
    }catch(const std::invalid_argument&){}catch(const Refused&){}
  }
  return state;
}
Json recover_uid(SnapshotBridge& bridge,const aii::uid::BoundPolicies& policies,
    const std::string& profile_hash,const std::string& captures_hash,const std::string& upload,
    const std::string& registry_hash){
  expected(profile_hash);expected(captures_hash);
  if(!registry_hash.empty()){expected(registry_hash);require(registry_hash!="absent","speaker registry recovery needs its observed digest");}
  const auto before=inspect_uid(bridge,policies);
  require(before.profile_hash()==profile_hash&&before.captures_hash()==captures_hash&&
      (registry_hash.empty()||(before.registry_recovery()&&before.registry_hash()==registry_hash)),
      "UID bytes changed since confirmation; inspect again and obtain fresh confirmation");
  // A confirmation that did not see the registry never leaves it blocking identity.
  require(!registry_hash.empty()||!before.registry_recovery(),
      "speaker registry also needs recovery; call speaker.list and confirm every reported digest");
  const auto preserve=[&](const Json& record,const std::string& stage) {
    const auto bytes=encode(record),id=hash(bytes);
    bool absent=false;const auto existing=bridge.read(&absent,SnapshotBridge::Store::Recovery,id);
    require(absent||existing==bytes,"recovery archive differs; nothing changed");
    // Re-attest identical archives on a separately confirmed retry, including
    // fsync. Seeing bytes after an uncertain publication is not durability proof.
    auto saved=bridge.publish(bytes,absent?"":id,absent,stage,SnapshotBridge::Store::Recovery,id);
    require(durable(saved),"recovery archive durability unresolved; original stores unchanged");
    return id;
  };
  auto archive=object();put(archive,"enrollment_sha256",string(profile_hash));put(archive,"captures_sha256",string(captures_hash));
  put(archive,"enrollment_b64",before.profile_absent?null():string(aii::uid::encode_base64(before.profile)));
  put(archive,"captures_b64",before.captures_absent?null():string(aii::uid::encode_base64(before.captures)));
  const auto id=preserve(archive,upload);
  // A separate archive keeps each within the recovery store's byte bound.
  std::string registry_id;
  if(!registry_hash.empty()){
    auto registry=object();put(registry,"speaker_registry_sha256",string(registry_hash));
    put(registry,"speaker_registry_b64",string(aii::uid::encode_base64(before.registry)));
    try{registry_id=preserve(registry,hash(upload+"speaker_registry archive"));}
    catch(const std::exception& e){
      // Nothing is reset yet, but the first archive is durable and holds biometric bytes.
      throw Refused("UID recovery incomplete; enrollment and captures preserved in uid/recovery-"+id+
          ".json; speaker registry archive unresolved and no store changed; inspect all stores before a fresh confirmation: "+e.what());
    }
  }
  try{
    const auto& policy=policies.current();
    auto pending=bridge.publish(aii::uid::write_captures({0,{}},policy.policy.embedding_binding),
        before.captures_absent?"":captures_hash,before.captures_absent,hash(upload+"captures"),SnapshotBridge::Store::PendingCaptures);
    require(durable(pending),"pending-capture reset durability unresolved");
    auto profile=bridge.publish(aii::uid::write_snapshot({policy.policy,0,{}},policy),
        before.profile_absent?"":profile_hash,before.profile_absent,hash(upload+"enrollment"));
    require(durable(profile),"enrollment reset durability unresolved");
    auto out=object();put(out,"recovery_archive_sha256",string(id));put(out,"recovery_durable",boolean(true));
    put(out,"publication",std::move(profile));put(out,"capture_publication",std::move(pending));
    if(!registry_hash.empty()){
      auto speakers=bridge.publish(aii::uid::write_registry({0,{policy.policy,0,{}},{}},policy),
          registry_hash,false,hash(upload+"speaker_registry"),SnapshotBridge::Store::SpeakerRegistry);
      require(durable(speakers),"speaker registry reset durability unresolved");
      put(out,"speaker_registry_recovery_archive_sha256",string(registry_id));
      put(out,"speaker_registry_publication",std::move(speakers));
    }
    return out;
  }catch(const std::exception& e){
    const auto kept=registry_id.empty()?"":" and uid/recovery-"+registry_id+".json";
    throw Refused("UID recovery incomplete; originals preserved in uid/recovery-"+id+".json"+kept+
        "; inspect all stores before a fresh confirmation: "+e.what());
  }
}
}
