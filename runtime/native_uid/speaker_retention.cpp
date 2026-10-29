#include "speaker_retention.h"
#include "../native/session/worker_json.h"
#include "../native/vendor/picosha2/picosha2.h"
#include <algorithm>
#include <set>

namespace aii::uid {
namespace {
using namespace aii::voice::wire;
constexpr uint64_t limit=9007199254740991ULL;
constexpr size_t max_rows=256;
void canonical_uuid(const std::string& s) {
  require(s.size()==36&&s[8]=='-'&&s[13]=='-'&&s[18]=='-'&&s[23]=='-'&&s[14]=='4'&&
      std::string("89ab").find(s[19])!=std::string::npos,"heard day UUID must be canonical random UUIDv4");
  for(size_t i=0;i<s.size();++i)if(i!=8&&i!=13&&i!=18&&i!=23)
    require(std::string("0123456789abcdef").find(s[i])!=std::string::npos,"heard day UUID is not canonical");
}
bool enrolled_bucket(const SpeakerBucket& b,const Snapshot* enrolled) {
  return b.enrollment&&(!enrolled||bound_enrollment(b,*enrolled));
}
std::map<std::string,std::vector<const SpeakerBucket*>> link_groups(const SpeakerRegistry& r) {
  std::map<std::string,std::vector<const SpeakerBucket*>> groups;
  for(const auto& b:r.buckets)groups[canonical_speaker(r,b.uuid)].push_back(&b);
  return groups;
}
std::optional<uint64_t> group_expiry(const std::vector<const SpeakerBucket*>& group,const HeardDays& heard,
    uint32_t days,const Snapshot* enrolled) {
  uint64_t last=0;
  for(const auto* b:group) {
    if(enrolled_bucket(*b,enrolled))return std::nullopt;
    const auto day=heard.find(b->uuid);
    if(day==heard.end())return std::nullopt;
    last=std::max(last,day->second);
  }
  return last+days;
}
}
HeardDays read_heard(const std::string& raw) {
  require(!raw.empty()&&raw.size()<=65536,"heard days byte bound");
  auto root=parse(raw);
  require(cJSON_IsObject(root.get())&&cJSON_GetArraySize(root.get())==2&&field(root.get(),"schema")&&field(root.get(),"heard"),"heard days fields differ");
  require(integer(field(root.get(),"schema"))==1,"heard days schema differs");
  const auto* rows=field(root.get(),"heard");
  require(cJSON_IsArray(rows)&&cJSON_GetArraySize(rows)<=int(max_rows),"heard days row count");
  HeardDays heard;
  for(auto* row=rows->child;row;row=row->next) {
    require(cJSON_IsObject(row)&&cJSON_GetArraySize(row)==2&&field(row,"day")&&field(row,"uuid"),"heard day fields differ");
    const auto id=str(field(row,"uuid"),36);canonical_uuid(id);
    require(heard.emplace(id,integer(field(row,"day"))).second,"heard day UUID repeated");
  }
  require(write_heard(heard)==raw,"heard days are not canonical");
  return heard;
}
std::string write_heard(const HeardDays& heard) {
  require(heard.size()<=max_rows,"heard days row count");
  auto root=object(),rows=own(cJSON_CreateArray());
  for(const auto& [id,day]:heard) {
    canonical_uuid(id);require(day<=limit,"heard day out of range");
    auto row=object();put(row,"day",number(day));put(row,"uuid",string(id));
    require(cJSON_AddItemToArray(rows.get(),row.get()),"heard days allocation");row.release();
  }
  put(root,"schema",number(1));put(root,"heard",std::move(rows));
  return encode(root);
}
ExpiryPass expire_unenrolled(const std::string& raw,const PolicyDocument& p,const HeardDays& before,
    uint64_t today,uint32_t days,const Snapshot* enrolled) {
  require(days>0,"voiceprint expiry requires a positive number of days");
  require(today<=limit-days,"voiceprint expiry day out of range");
  auto r=read_registry(raw,p);
  ExpiryPass pass;
  for(const auto& b:r.buckets)if(!enrolled_bucket(b,enrolled)) {
    const auto day=before.find(b.uuid);
    pass.heard[b.uuid]=day==before.end()?today:day->second;
  }
  std::set<std::string> gone;
  for(const auto& group:link_groups(r)) {
    const auto day=group_expiry(group.second,pass.heard,days,enrolled);
    if(day&&today>=*day)for(const auto* b:group.second)gone.insert(b->uuid);
  }
  const auto base=picosha2::hash256_hex_string(raw);
  if(gone.empty()) {
    pass.registry={base,raw,"","unchanged","no_voiceprint_expired",r.revision,std::nullopt};
    return pass;
  }
  require(r.revision<limit,"speaker registry revision exhausted");++r.revision;
  r.buckets.erase(std::remove_if(r.buckets.begin(),r.buckets.end(),[&](const auto& b){return gone.count(b.uuid)>0;}),r.buckets.end());
  r.profiles.speakers.erase(std::remove_if(r.profiles.speakers.begin(),r.profiles.speakers.end(),
      [&](const auto& profile){return gone.count(profile.id)>0;}),r.profiles.speakers.end());
  r.profiles.revision=r.revision;
  auto document=write_registry(r,p);(void)read_registry(document,p);
  for(const auto& id:gone)pass.heard.erase(id);
  pass.registry={base,std::move(document),"","expired","unenrolled_voiceprint_expired",r.revision,std::nullopt};
  pass.expired.assign(gone.begin(),gone.end());
  return pass;
}
HeardDays heard_speaker(const SpeakerRegistry& r,HeardDays heard,const std::string& id,uint64_t today,const Snapshot* enrolled) {
  require(today<=limit,"speaker heard day out of range");canonical_uuid(id);
  const auto found=std::find_if(r.buckets.begin(),r.buckets.end(),[&](const auto& b){return b.uuid==id;});
  require(found!=r.buckets.end(),"speaker UUID not found");
  if(enrolled_bucket(*found,enrolled))return heard;
  auto& day=heard[id];
  day=std::max(day,today);
  return heard;
}
std::optional<uint64_t> expiry_day(const SpeakerRegistry& r,const SpeakerBucket& bucket,const HeardDays& heard,
    uint32_t days,const Snapshot* enrolled) {
  if(!days)return std::nullopt;
  const auto groups=link_groups(r);
  const auto group=groups.find(canonical_speaker(r,bucket.uuid));
  require(group!=groups.end(),"speaker UUID not found");
  return group_expiry(group->second,heard,days,enrolled);
}
}
