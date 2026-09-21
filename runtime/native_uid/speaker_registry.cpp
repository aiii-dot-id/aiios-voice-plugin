#include "speaker_registry.h"
#include "../native/session/worker_json.h"
#include "../native/vendor/picosha2/picosha2.h"
#include <algorithm>
#include <set>

namespace aii::uid {
namespace {
using namespace aii::voice::wire;
constexpr uint64_t limit=9007199254740991ULL;
constexpr size_t max_bytes=8u<<20,max_buckets=256,max_associations=1024;
void fields(const cJSON* j,std::initializer_list<const char*> names) {
  require(cJSON_IsObject(j)&&cJSON_GetArraySize(j)==int(names.size()),"registry fields differ");
  for(auto name:names)require(field(j,name),"registry field missing");
}
void uuid(const std::string& s) {
  require(s.size()==36&&s[8]=='-'&&s[13]=='-'&&s[18]=='-'&&s[23]=='-'&&s[14]=='4'&&
      std::string("89ab").find(s[19])!=std::string::npos,"speaker UUID must be canonical random UUIDv4");
  for(size_t i=0;i<s.size();++i)if(i!=8&&i!=13&&i!=18&&i!=23)
    require(std::string("0123456789abcdef").find(s[i])!=std::string::npos,"speaker UUID is not canonical");
}
void text(const std::string& value) {
  const auto points=unicode_scalars(value);
  require(points.size()<=128&&std::all_of(points.begin(),points.end(),[](uint32_t c){return c>=32&&c!=127;}),"invalid speaker association text");
}
std::string optional_text(const cJSON* j) {return cJSON_IsNull(j)?"":str(j,512);}
Json nullable(const std::string& s){return s.empty()?null():string(s);}
void append(Json& array,Json row){require(cJSON_AddItemToArray(array.get(),row.get()),"registry allocation");row.release();}
void advance(SpeakerRegistry& r){require(r.revision<limit,"speaker registry revision exhausted");++r.revision;}
RegistryChange prepared(const std::string& base,const SpeakerRegistry& r,const PolicyDocument& p,
    const std::string& id,const std::string& continuity,const std::string& reason) {
  auto document=write_registry(r,p);(void)read_registry(document,p);
  return {picosha2::hash256_hex_string(base),std::move(document),id,continuity,reason,r.revision};
}
}
std::string write_registry(const SpeakerRegistry& r,const PolicyDocument& p) {
  const auto policy=read_policy(p.canonical);
  require(r.revision<=limit&&r.buckets.size()<=max_buckets,"speaker registry capacity/revision exceeded");
  require(r.profiles.policy.fingerprint==policy.policy.fingerprint,"speaker registry policy differs");
  const auto profiles=write_snapshot(r.profiles,policy);
  auto root=object(),buckets=own(cJSON_CreateArray());
  std::string previous;size_t history_count=0;std::set<std::string> ids;std::set<uint64_t> revisions;
  for(const auto& b:r.buckets) {
    uuid(b.uuid);require(b.uuid>previous&&b.created_revision>0&&b.created_revision<=r.revision,"speaker registry ordering/revision");
    require(revisions.insert(b.created_revision).second,"reused registry mutation revision");
    previous=b.uuid;ids.insert(b.uuid);auto row=object(),history=own(cJSON_CreateArray());
    uint64_t revision=b.created_revision;
    for(const auto& a:b.associations) {
      require(++history_count<=max_associations&&a.revision>revision&&a.revision<=r.revision,"speaker association history bound/order");
      require(revisions.insert(a.revision).second,"reused registry mutation revision");
      revision=a.revision;text(a.label);text(a.external_id);auto entry=object();
      put(entry,"revision",number(a.revision));put(entry,"label",nullable(a.label));put(entry,"external_id",nullable(a.external_id));append(history,std::move(entry));
    }
    put(row,"uuid",string(b.uuid));put(row,"created_revision",number(b.created_revision));put(row,"associations",std::move(history));append(buckets,std::move(row));
  }
  require(r.profiles.revision<=r.revision,"profile revision exceeds registry");
  for(const auto& profile:r.profiles.speakers)
    require(ids.count(profile.id)&&profile.label==profile.id,"profile must belong to anonymous bucket; labels are separate");
  // Keep the established snapshot's exact canonical bytes. Re-encoding its
  // floating-point policy through cJSON would change e.g. 1.0 into 1.
  put(root,"schema",number(1));put(root,"revision",number(r.revision));put(root,"profile_document",string(profiles));put(root,"buckets",std::move(buckets));
  auto out=encode(root);require(out.size()<=max_bytes,"speaker registry byte bound");return out;
}
SpeakerRegistry read_registry(const std::string& raw,const PolicyDocument& p) {
  require(!raw.empty()&&raw.size()<=max_bytes,"speaker registry byte bound");auto root=parse(raw);
  fields(root.get(),{"schema","revision","profile_document","buckets"});require(integer(field(root.get(),"schema"))==1,"speaker registry schema differs");
  SpeakerRegistry r;r.revision=integer(field(root.get(),"revision"));
  r.profiles=read_snapshot(str(field(root.get(),"profile_document"),max_bytes),p);
  const auto* list=field(root.get(),"buckets");require(cJSON_IsArray(list)&&cJSON_GetArraySize(list)<=int(max_buckets),"speaker registry bucket count");
  for(auto* item=list->child;item;item=item->next) {
    fields(item,{"uuid","created_revision","associations"});SpeakerBucket b{str(field(item,"uuid"),36),integer(field(item,"created_revision")),{}};
    const auto* history=field(item,"associations");require(cJSON_IsArray(history)&&cJSON_GetArraySize(history)<=int(max_associations),"speaker association count");
    for(auto* entry=history->child;entry;entry=entry->next){fields(entry,{"revision","label","external_id"});
      b.associations.push_back({integer(field(entry,"revision")),optional_text(field(entry,"label")),optional_text(field(entry,"external_id"))});}
    r.buckets.push_back(std::move(b));
  }
  require(write_registry(r,p)==raw,"speaker registry is not canonical");return r;
}
RegistryChange observe_speaker(const std::string& raw,const PolicyDocument& p,uint64_t expected,
    const std::string& random_uuid,const std::optional<Sample>& sample) {
  auto r=read_registry(raw,p);require(r.revision==expected,"stale speaker registry revision");uuid(random_uuid);
  require(std::none_of(r.buckets.begin(),r.buckets.end(),[&](const auto& b){return b.uuid==random_uuid;}),"speaker UUID collision");
  std::string continuity="provisional",reason="speaker_specific_evidence_unavailable";
  if(sample) {
    // A single-recording profile requires the separately bound guided policy;
    // never change an older policy's sample count to make a match possible.
    require(p.policy.minimum_enrollment_samples==1,"anonymous matching requires the bound single-recording policy");
    validate({p.policy,1,{{random_uuid,random_uuid,{*sample}}}},p.policy);
    const auto decision=identify(r.profiles,p.policy,sample->embedding,p.policy.embedding_binding);
    if(decision.outcome=="known")return prepared(raw,r,p,decision.speaker_id,"matched","acoustic_profile_match");
    if(decision.outcome=="ambiguous")reason="ambiguous_profile_match";
    else {continuity="new_profile";reason="no_matching_profile";}
  }
  require(r.buckets.size()<max_buckets,"speaker registry full; explicit retention action required");advance(r);
  r.buckets.push_back({random_uuid,r.revision,{}});
  std::sort(r.buckets.begin(),r.buckets.end(),[](const auto& a,const auto& b){return a.uuid<b.uuid;});
  if(continuity=="new_profile") {
    r.profiles.speakers.push_back({random_uuid,random_uuid,{*sample}});
    std::sort(r.profiles.speakers.begin(),r.profiles.speakers.end(),[](const auto& a,const auto& b){return a.id<b.id;});
    r.profiles.revision=r.revision;
  }
  return prepared(raw,r,p,random_uuid,continuity,reason);
}
RegistryChange associate_speaker(const std::string& raw,const PolicyDocument& p,uint64_t expected,
    const std::string& id,const std::string& label,const std::string& external_id) {
  auto r=read_registry(raw,p);require(r.revision==expected,"stale speaker registry revision");uuid(id);text(label);text(external_id);
  const auto found=std::find_if(r.buckets.begin(),r.buckets.end(),[&](const auto& b){return b.uuid==id;});
  require(found!=r.buckets.end(),"speaker UUID not found");
  if((found->associations.empty()&&label.empty()&&external_id.empty())||
     (!found->associations.empty()&&found->associations.back().label==label&&found->associations.back().external_id==external_id))
    return prepared(raw,r,p,id,"unchanged","association_already_current");
  advance(r);found->associations.push_back({r.revision,label,external_id});
  return prepared(raw,r,p,id,"associated","metadata_only_not_authorization");
}
RegistryChange forget_speaker(const std::string& raw,const PolicyDocument& p,uint64_t expected,const std::string& id) {
  auto r=read_registry(raw,p);require(r.revision==expected,"stale speaker registry revision");uuid(id);
  const auto found=std::find_if(r.buckets.begin(),r.buckets.end(),[&](const auto& b){return b.uuid==id;});
  require(found!=r.buckets.end(),"speaker UUID not found");advance(r);r.buckets.erase(found);
  r.profiles.speakers.erase(std::remove_if(r.profiles.speakers.begin(),r.profiles.speakers.end(),
      [&](const auto& profile){return profile.id==id;}),r.profiles.speakers.end());
  r.profiles.revision=r.revision;
  return prepared(raw,r,p,id,"forgotten","confirmed_profile_and_metadata_removal");
}
}
