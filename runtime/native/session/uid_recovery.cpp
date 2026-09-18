#include "uid_recovery.h"
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
bool UIDInspection::needs_recovery() const {
  return (profile_state!="ready"&&profile_state!="absent")||(captures_state!="ready"&&captures_state!="absent");
}
Json UIDInspection::report() const {
  auto out=object();put(out,"enrollment_state",string(profile_state));put(out,"captures_state",string(captures_state));
  put(out,"required",boolean(needs_recovery()));
  put(out,"enrollment_sha256",string(profile_hash()));put(out,"captures_sha256",string(captures_hash()));
  if(!profile_model.empty())put(out,"stored_embedding_binding",string(profile_model));
  return out;
}
UIDInspection inspect_uid(SnapshotBridge& bridge,const aii::uid::BoundPolicies& policies){
  UIDInspection state;
  // Storage errors propagate. Only typed FS_NOT_FOUND means absent.
  state.profile=bridge.read(&state.profile_absent);
  state.captures=bridge.read(&state.captures_absent,SnapshotBridge::Store::PendingCaptures);
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
  return state;
}
Json recover_uid(SnapshotBridge& bridge,const aii::uid::BoundPolicies& policies,
    const std::string& profile_hash,const std::string& captures_hash,const std::string& upload){
  expected(profile_hash);expected(captures_hash);
  const auto before=inspect_uid(bridge,policies);
  require(before.profile_hash()==profile_hash&&before.captures_hash()==captures_hash,
      "UID bytes changed since confirmation; inspect again and obtain fresh confirmation");
  auto archive=object();put(archive,"enrollment_sha256",string(profile_hash));put(archive,"captures_sha256",string(captures_hash));
  put(archive,"enrollment_b64",before.profile_absent?null():string(aii::uid::encode_base64(before.profile)));
  put(archive,"captures_b64",before.captures_absent?null():string(aii::uid::encode_base64(before.captures)));
  const auto bytes=encode(archive),id=hash(bytes);
  bool absent=false;const auto existing=bridge.read(&absent,SnapshotBridge::Store::Recovery,id);
  require(absent||existing==bytes,"recovery archive differs; nothing changed");
  // Re-attest identical archives on a separately confirmed retry, including
  // fsync. Seeing bytes after an uncertain publication is not durability proof.
  auto saved=bridge.publish(bytes,absent?"":id,absent,upload,SnapshotBridge::Store::Recovery,id);
  require(durable(saved),"recovery archive durability unresolved; original stores unchanged");
  try{
    const auto& policy=policies.current();
    auto pending=bridge.publish(aii::uid::write_captures({0,{}},policy.policy.embedding_binding),
        before.captures_absent?"":captures_hash,before.captures_absent,hash(upload+"captures"),SnapshotBridge::Store::PendingCaptures);
    require(durable(pending),"pending-capture reset durability unresolved");
    auto profile=bridge.publish(aii::uid::write_snapshot({policy.policy,0,{}},policy),
        before.profile_absent?"":profile_hash,before.profile_absent,hash(upload+"enrollment"));
    require(durable(profile),"enrollment reset durability unresolved");
    auto out=object();put(out,"recovery_archive_sha256",string(id));put(out,"recovery_durable",boolean(true));
    put(out,"publication",std::move(profile));put(out,"capture_publication",std::move(pending));return out;
  }catch(const std::exception& e){
    throw Refused("UID recovery incomplete; originals preserved in uid/recovery-"+id+".json; inspect both stores before a fresh confirmation: "+e.what());
  }
}
}
