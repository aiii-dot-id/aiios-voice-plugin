#include "speaker_registry.h"
#include "enrollment.h"
#include "../native/session/worker_json.h"
#include "../native/vendor/picosha2/picosha2.h"
#include <algorithm>
#include <set>
#include <map>

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
  return {picosha2::hash256_hex_string(base),std::move(document),id,continuity,reason,r.revision,std::nullopt};
}
}
std::string canonical_speaker(const SpeakerRegistry& r,const std::string& id) {
  const auto b=std::find_if(r.buckets.begin(),r.buckets.end(),[&](const auto& b){return b.uuid==id;});
  require(b!=r.buckets.end(),"speaker UUID not found");
  return b->links.empty()?id:b->links.back().second;
}
const Speaker* bound_enrollment(const SpeakerBucket& bucket,const Snapshot& snapshot) {
  if(!bucket.enrollment)return nullptr;
  for(const auto& speaker:snapshot.speakers)if(speaker.id==bucket.enrollment->id &&
      std::all_of(bucket.enrollment->evidence.begin(),bucket.enrollment->evidence.end(),[&](const auto& digest){
        return std::any_of(speaker.samples.begin(),speaker.samples.end(),[&](const auto& sample){return sample.audio_sha256==digest;});
      }))return &speaker;
  return nullptr;
}
Snapshot anonymous_matching_profiles(const SpeakerRegistry& r) {
  validate(r.profiles,r.profiles.policy);
  auto gallery=r.profiles;
  gallery.speakers.erase(std::remove_if(gallery.speakers.begin(),gallery.speakers.end(),
    [](const auto& s){return s.samples.size()<anonymous_profile_minimum_samples;}),gallery.speakers.end());
  return gallery;
}
std::string write_registry(const SpeakerRegistry& r,const PolicyDocument& p) {
  const auto policy=read_policy(p.canonical);
  require(r.revision<=limit&&r.buckets.size()<=max_buckets,"speaker registry capacity/revision exceeded");
  require(r.profiles.policy.fingerprint==policy.policy.fingerprint,"speaker registry policy differs");
  const auto profiles=write_snapshot(r.profiles,policy);
  auto root=object(),buckets=own(cJSON_CreateArray());
  std::string previous;size_t history_count=0;std::set<std::string> ids,enrolled_ids;std::set<uint64_t> revisions;
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
    put(row,"uuid",string(b.uuid));put(row,"created_revision",number(b.created_revision));put(row,"associations",std::move(history));
    if(b.enrollment) {
      const auto& ref=*b.enrollment;text(ref.id);
      require(!ref.id.empty()&&enrolled_ids.insert(ref.id).second,"duplicate/empty enrolled identity binding");
      require(!ref.evidence.empty()&&ref.evidence.size()<=8,"enrollment evidence count");
      auto binding=object(),evidence=own(cJSON_CreateArray());std::string prior;
      for(const auto& digest:ref.evidence) {
        require(digest.size()==64&&digest.find_first_not_of("0123456789abcdef")==std::string::npos&&digest>prior,
          "enrollment evidence binding invalid");
        prior=digest;append(evidence,string(digest));
      }
      put(binding,"id",string(ref.id));put(binding,"evidence",std::move(evidence));put(row,"enrollment",std::move(binding));
    }
    if(!b.links.empty()) {
      auto links=own(cJSON_CreateArray());uint64_t last=b.created_revision;
      for(const auto& link:b.links) {
        require(++history_count<=max_associations && link.first>last && link.first<=r.revision &&
          revisions.insert(link.first).second,"speaker link history bound/order");
        last=link.first;uuid(link.second);auto entry=object();
        put(entry,"revision",number(link.first));put(entry,"target_uuid",string(link.second));append(links,std::move(entry));
      }
      const auto target=canonical_speaker(r,b.uuid);
      require(canonical_speaker(r,target)==target,"speaker link chains/cycles refused");
      put(row,"links",std::move(links));
    }
    append(buckets,std::move(row));
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
    if(field(item,"enrollment")) {
      if(field(item,"links"))fields(item,{"uuid","created_revision","associations","links","enrollment"});
      else fields(item,{"uuid","created_revision","associations","enrollment"});
    } else if(field(item,"links"))fields(item,{"uuid","created_revision","associations","links"});
    else fields(item,{"uuid","created_revision","associations"});
    SpeakerBucket b{str(field(item,"uuid"),36),integer(field(item,"created_revision")),{}, {}};
    if(const auto* binding=field(item,"enrollment")) {
      fields(binding,{"id","evidence"});EnrollmentReference ref{str(field(binding,"id"),512),{}};
      const auto* evidence=field(binding,"evidence");require(cJSON_IsArray(evidence)&&cJSON_GetArraySize(evidence)<=8,"enrollment evidence count");
      for(auto* digest=evidence->child;digest;digest=digest->next)ref.evidence.push_back(str(digest,64));
      b.enrollment=std::move(ref);
    }
    const auto* history=field(item,"associations");require(cJSON_IsArray(history)&&cJSON_GetArraySize(history)<=int(max_associations),"speaker association count");
    for(auto* entry=history->child;entry;entry=entry->next){fields(entry,{"revision","label","external_id"});
      b.associations.push_back({integer(field(entry,"revision")),optional_text(field(entry,"label")),optional_text(field(entry,"external_id"))});}
    if(const auto* links=field(item,"links")) {
      require(cJSON_IsArray(links)&&cJSON_GetArraySize(links)>0&&cJSON_GetArraySize(links)<=int(max_associations),"speaker link count");
      for(auto* link=links->child;link;link=link->next) {
        fields(link,{"revision","target_uuid"});
        b.links.emplace_back(integer(field(link,"revision")),str(field(link,"target_uuid"),36));
      }
    }
    r.buckets.push_back(std::move(b));
  }
  require(write_registry(r,p)==raw,"speaker registry is not canonical");return r;
}
RegistryChange observe_speaker(const std::string& raw,const PolicyDocument& p,uint64_t expected,
    const std::string& random_uuid,const std::optional<Sample>& sample,ProfileAdmission admission,
    const std::optional<Sample>& corroborating_sample,const Snapshot* enrolled) {
  auto r=read_registry(raw,p);require(r.revision==expected,"stale speaker registry revision");uuid(random_uuid);
  require(std::none_of(r.buckets.begin(),r.buckets.end(),[&](const auto& b){return b.uuid==random_uuid;}),"speaker UUID collision");
  std::string continuity="provisional",reason="speaker_specific_evidence_unavailable";
  std::optional<Decision> match;
  if(sample) {
    // A single-recording profile requires the separately bound guided policy;
    // never change an older policy's sample count to make a match possible.
    require(p.policy.minimum_enrollment_samples==1,"anonymous matching requires the bound single-recording policy");
    validate({p.policy,1,{{random_uuid,random_uuid,{*sample}}}},p.policy);
    const auto decision=identify(anonymous_matching_profiles(r),p.policy,sample->embedding,p.policy.embedding_binding);
    match=decision;
    if(decision.outcome=="known") {
      // Resolve only AFTER the unchanged acoustic decision. Linked profiles
      // remain separate competitors: a label/link never relaxes a margin.
      auto result=prepared(raw,r,p,canonical_speaker(r,decision.speaker_id),"matched","acoustic_profile_match");
      result.match=match;return result;
    }
    if(decision.outcome=="unknown") {
      auto legacy=identify(r.profiles,p.policy,sample->embedding,p.policy.embedding_binding);
      if(legacy.outcome=="known" || legacy.outcome=="ambiguous") {
        // Preserve the legacy bucket and its diagnostics; neither assert it
        // as identified nor manufacture a replacement UUID for the same voice.
        legacy.outcome="unknown";legacy.speaker_id.clear();legacy.label.clear();
        legacy.reason="anonymous_profile_needs_corroboration";
        auto result=prepared(raw,r,p,"","provisional",legacy.reason);
        result.match=legacy;return result;
      }
    }
    if(decision.outcome=="ambiguous")reason="ambiguous_profile_match";
    else {continuity="new_profile";reason="no_matching_profile";}
  }
  if(continuity=="provisional") {
    // A final already has a session/sequence/track reference. Missing or
    // ambiguous evidence is not a new person and must not consume durable
    // identity capacity. Preserve all older rows without migrating them.
    auto result=prepared(raw,r,p,"",continuity,reason);result.match=match;return result;
  }
  // Non-recognition is not positive novelty evidence. Only the composition's
  // bounded, distinct-utterance corroboration owner may admit a new profile.
  if(admission!=ProfileAdmission::Corroborated) {
    auto result=prepared(raw,r,p,"","provisional","speaker_profile_pending");
    result.match=match;return result;
  }
  require(sample&&corroborating_sample,"corroborated profile requires two recordings");
  auto pair=std::vector<Sample>{*sample,*corroborating_sample};
  std::sort(pair.begin(),pair.end(),[](const auto& a,const auto& b){return a.audio_sha256<b.audio_sha256;});
  validate({p.policy,1,{{random_uuid,random_uuid,pair}}},p.policy);
  double agreement=0;
  Vector combined(embedding_dimensions(p.policy.embedding_binding));
  for(size_t i=0;i<combined.size();++i) {
    agreement+=pair[0].embedding[i]*pair[1].embedding[i];
    combined[i]=pair[0].embedding[i]+pair[1].embedding[i];
  }
  require(agreement>=p.policy.threshold,"corroborating recordings disagree");
  // Two individually rejected samples can average into an existing profile.
  // That is uncertain continuity, not proof of a new person; leave it pending.
  const auto combined_match=identify(r.profiles,p.policy,combined,p.policy.embedding_binding);
  if(combined_match.outcome=="known"||combined_match.outcome=="ambiguous") {
    // Keep the final utterance's diagnostics bound to that utterance. The
    // aggregate is used only as a veto; reporting its score as this final's
    // own score would be a misleading attribution trace.
    auto result=prepared(raw,r,p,"","provisional","corroborated_profile_conflicts_with_existing");
    result.match=match;return result;
  }
  // A threshold rejection is not evidence of a new human. Condition changes
  // can push each recording below acceptance while leaving it close to an
  // existing profile. Do not mint a second durable UUID in that uncertainty.
  const auto near=[&](const Snapshot& gallery,const Sample& candidate) {
    const auto d=identify(gallery,gallery.policy,candidate.embedding,p.policy.embedding_binding);
    return d.score && *d.score>=gallery.policy.threshold-gallery.policy.minimum_margin;
  };
  if((near(r.profiles,pair[0]) || near(r.profiles,pair[1])) ||
     (enrolled && (near(*enrolled,pair[0]) || near(*enrolled,pair[1]) ||
                   near(*enrolled,Sample{pair[0].audio_sha256,combined})))) {
    auto result=prepared(raw,r,p,"","provisional","corroborated_profile_near_existing");
    result.match=match;return result;
  }
  require(r.buckets.size()<max_buckets,"speaker registry full; explicit retention action required");advance(r);
  r.buckets.push_back({random_uuid,r.revision,{}, {}});
  std::sort(r.buckets.begin(),r.buckets.end(),[](const auto& a,const auto& b){return a.uuid<b.uuid;});
  if(continuity=="new_profile") {
    r.profiles.speakers.push_back({random_uuid,random_uuid,std::move(pair)});
    std::sort(r.profiles.speakers.begin(),r.profiles.speakers.end(),[](const auto& a,const auto& b){return a.id<b.id;});
    r.profiles.revision=r.revision;
  }
  auto result=prepared(raw,r,p,random_uuid,continuity,reason);
  result.match=match;return result;
}
std::string match_diagnostics(const RegistryChange& change,const PolicyDocument& policy) {
  if(!change.match)return {};
  const auto& d=*change.match;auto out=aii::voice::wire::object();
  put(out,"outcome",string(d.outcome));put(out,"reason",string(d.reason));
  put(out,"candidate_count",number(d.candidate_count));
  if(!d.candidate_id.empty())put(out,"candidate_uuid",string(d.candidate_id));
  if(d.score)put(out,"score",own(cJSON_CreateNumber(*d.score)));
  if(d.margin)put(out,"margin",own(cJSON_CreateNumber(*d.margin)));
  put(out,"threshold",own(cJSON_CreateNumber(policy.policy.threshold)));
  put(out,"minimum_margin",own(cJSON_CreateNumber(policy.policy.minimum_margin)));
  put(out,"profile_revision",string(std::to_string(d.enrollment_revision)));
  put(out,"policy_sha256",string(d.policy_sha256));
  put(out,"embedding_binding",string(policy.policy.embedding_binding));
  return encode(out);
}
RegistryChange bind_enrolled_speaker(const std::string& raw,const PolicyDocument& p,uint64_t expected,
    const std::string& random,const Speaker& speaker,const std::string& existing) {
  auto r=read_registry(raw,p);require(r.revision==expected,"stale speaker registry revision");
  // Validate selected enrollment evidence without reinterpreting its label as UUID.
  validate({p.policy,1,{speaker}},p.policy);
  for(const auto& b:r.buckets)if(b.enrollment&&b.enrollment->id==speaker.id) {
    require(bound_enrollment(b,{p.policy,1,{speaker}}),"enrollment binding changed; retained UUID is unresolved");
    require(existing.empty()||canonical_speaker(r,existing)==canonical_speaker(r,b.uuid),"enrolled UUID conflicts with existing anonymous identity");
    return prepared(raw,r,p,canonical_speaker(r,b.uuid),"matched","enrolled_uuid_retained");
  }
  uuid(random);std::string id=existing;
  if(id.empty()) {
    require(r.buckets.size()<max_buckets&&std::none_of(r.buckets.begin(),r.buckets.end(),[&](const auto& b){return b.uuid==random;}),"speaker registry capacity or UUID collision");
    advance(r);id=random;r.buckets.push_back({id,r.revision,{},{},{}});
    std::sort(r.buckets.begin(),r.buckets.end(),[](const auto& a,const auto& b){return a.uuid<b.uuid;});
  } else {
    uuid(id);
    const auto profile=std::find_if(r.profiles.speakers.begin(),r.profiles.speakers.end(),[&](const auto& s){return s.id==id;});
    require(profile!=r.profiles.speakers.end()&&profile->samples.size()>=anonymous_profile_minimum_samples&&
      std::all_of(profile->samples.begin(),profile->samples.end(),[&](const auto& sample){
        return std::any_of(speaker.samples.begin(),speaker.samples.end(),[&](const auto& selected){
          return sample.audio_sha256==selected.audio_sha256&&sample.embedding==selected.embedding;
        });
      }),"existing UUID requires the exact enrolled anonymous evidence");
    advance(r);
  }
  auto found=std::find_if(r.buckets.begin(),r.buckets.end(),[&](const auto& b){return b.uuid==id;});
  require(found!=r.buckets.end()&&!found->enrollment,"enrollment UUID binding occupied or absent");
  EnrollmentReference ref{speaker.id,{}};
  for(const auto& sample:speaker.samples)ref.evidence.push_back(sample.audio_sha256);
  std::sort(ref.evidence.begin(),ref.evidence.end());found->enrollment=std::move(ref);
  return prepared(raw,r,p,canonical_speaker(r,id),"matched","enrolled_uuid_bound");
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
  require(found!=r.buckets.end(),"speaker UUID not found");
  for(const auto& b:r.buckets)require(b.uuid==id||canonical_speaker(r,b.uuid)!=id,"unlink referring speakers before forgetting their target");
  advance(r);r.buckets.erase(found);
  r.profiles.speakers.erase(std::remove_if(r.profiles.speakers.begin(),r.profiles.speakers.end(),
      [&](const auto& profile){return profile.id==id;}),r.profiles.speakers.end());
  r.profiles.revision=r.revision;
  return prepared(raw,r,p,id,"forgotten","confirmed_profile_and_metadata_removal");
}
RegistryChange link_speaker(const std::string& raw,const PolicyDocument& p,uint64_t expected,
    const std::string& source,const std::string& target) {
  auto r=read_registry(raw,p);require(r.revision==expected,"stale speaker registry revision");uuid(source);uuid(target);
  const auto current=canonical_speaker(r,source);
  require(canonical_speaker(r,target)==target || source==target,"target must be a canonical speaker, not an alias");
  if(current==target)return prepared(raw,r,p,source,"unchanged","speaker_link_already_current");
  if(source!=target) {
    for(const auto& id:{source,target})require(std::any_of(r.profiles.speakers.begin(),r.profiles.speakers.end(),
      [&](const auto& s){return s.id==id;})||std::any_of(r.buckets.begin(),r.buckets.end(),[&](const auto& b){return b.uuid==id&&b.enrollment.has_value();}),
      "speaker linking requires two acoustic profiles");
    for(const auto& b:r.buckets)require(b.uuid==source||canonical_speaker(r,b.uuid)!=source,"unlink referring speakers before linking their target");
  }
  advance(r);
  for(auto& b:r.buckets)if(b.uuid==source)b.links.emplace_back(r.revision,target);
  return prepared(raw,r,p,source,"linked","confirmed_identity_correction_not_authorization");
}
RegistryChange prepare_registry_reembedding(const std::string& raw,
    const PolicyDocument& previous,const PolicyDocument& target,
    uint64_t expected,const std::vector<Sample>& regenerated) {
  auto r=read_registry(raw,previous);
  require(r.revision==expected,"stale speaker registry revision");
  const auto next=prepare_reembedding(write_snapshot(r.profiles,previous),previous,target,regenerated);
  r.profiles=read_snapshot(next.snapshot,target);advance(r);r.profiles.revision=r.revision;
  return prepared(raw,r,target,"","reembedded","same_recordings_new_model");
}
PreparedSpeakerModelTransition prepare_speaker_model_transition(
    const std::string& enrollment,const std::string& registry,
    const PolicyDocument& previous,const PolicyDocument& target,
    uint64_t enrollment_revision,uint64_t registry_revision,
    const std::vector<Sample>& regenerated) {
  const auto enrolled=read_snapshot(enrollment,previous);
  const auto known=read_registry(registry,previous);
  require(enrolled.revision==enrollment_revision,"stale enrollment revision");
  require(known.revision==registry_revision,"stale speaker registry revision");
  require(regenerated.size()<=2*256*8,"paired reembedding evidence bound exceeded");
  std::map<std::string,Sample> evidence;
  for(const auto& sample:regenerated)
    require(evidence.emplace(sample.audio_sha256,sample).second,"duplicate reembedding evidence");
  std::set<std::string> consumed;
  auto select=[&](const Snapshot& snapshot) {
    std::vector<Sample> result;std::set<std::string> selected;
    for(const auto& speaker:snapshot.speakers)for(const auto& sample:speaker.samples) {
      const auto found=evidence.find(sample.audio_sha256);
      require(found!=evidence.end(),"original recording unavailable for reembedding");
      if(selected.insert(sample.audio_sha256).second)result.push_back(found->second);
      consumed.insert(sample.audio_sha256);
    }
    return result;
  };
  const auto enrollment_samples=select(enrolled),registry_samples=select(known.profiles);
  require(consumed.size()==evidence.size(),"reembedding contains unrelated recording");
  PreparedSpeakerModelTransition result{
    prepare_reembedding(enrollment,previous,target,enrollment_samples),
    prepare_registry_reembedding(registry,previous,target,registry_revision,registry_samples)};
  const auto next=read_snapshot(result.enrollment.snapshot,target);
  const auto next_registry=read_registry(result.registry.document,target);
  require(next_registry.buckets.size()==known.buckets.size(),"model transition changed speaker buckets");
  for(size_t i=0;i<known.buckets.size();++i) {
    const auto* before=bound_enrollment(known.buckets[i],enrolled);
    const auto* after=bound_enrollment(next_registry.buckets[i],next);
    require(bool(before)==bool(after)&&(!before||before->id==after->id),
        "model transition changed enrollment reference");
  }
  return result;
}
}
