#include "speaker_registry_store.h"
#include "../../native_uid/enrollment.h"
#include "../vendor/picosha2/picosha2.h"
#include <algorithm>
#include <cmath>
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
std::string label_for(const aii::uid::SpeakerBucket& bucket,const aii::uid::Snapshot* enrolled) {
  if(!bucket.associations.empty())return bucket.associations.back().label.empty()?"unknown":bucket.associations.back().label;
  const auto* named=enrolled?aii::uid::bound_enrollment(bucket,*enrolled):nullptr;
  return named&&!named->label.empty()?named->label:"unknown";
}
Json projection(const aii::uid::SpeakerRegistry& registry,const aii::uid::Snapshot* enrolled=nullptr) {
  auto out=object(),rows=own(cJSON_CreateArray());
  put(out,"registry_revision",string(std::to_string(registry.revision)));
  for(const auto& bucket:registry.buckets) {
    auto row=object();put(row,"speaker_uuid",string(bucket.uuid));
    put(row,"canonical_uuid",string(aii::uid::canonical_speaker(registry,bucket.uuid)));
    if(!bucket.links.empty())put(row,"link_revision",string(std::to_string(bucket.links.back().first)));
    const auto profile=std::find_if(registry.profiles.speakers.begin(),registry.profiles.speakers.end(),
        [&](const auto& p){return p.id==bucket.uuid;});
    const auto* named=enrolled?aii::uid::bound_enrollment(bucket,*enrolled):nullptr;
    put(row,"profile_available",boolean(named||profile!=registry.profiles.speakers.end()));
    const bool ready=named||(profile!=registry.profiles.speakers.end() &&
      profile->samples.size()>=aii::uid::anonymous_profile_minimum_samples);
    put(row,"matching_ready",boolean(ready));
    if(!ready)put(row,"matching_reason",string(profile==registry.profiles.speakers.end()?
      "speaker_specific_evidence_unavailable":"anonymous_profile_needs_corroboration"));
    if(profile!=registry.profiles.speakers.end()) {
      // Expose the exact evidence identities already bound to this acoustic
      // profile. These digests support a reviewed, reversible speaker.link;
      // they never turn a nearby voice into an accepted match.
      auto evidence=own(cJSON_CreateArray());
      for(const auto& sample:profile->samples) {
        auto digest=string(sample.audio_sha256);
        require(cJSON_AddItemToArray(evidence.get(),digest.get()),"speaker evidence projection allocation");
        digest.release();
      }
      put(row,"evidence_sha256",std::move(evidence));
    }
    put(row,"display_label",string(label_for(bucket,enrolled)));
    if(bucket.enrollment)put(row,"enrollment_id",string(bucket.enrollment->id));
    if(!bucket.associations.empty()) {
      put(row,"external_id",string(bucket.associations.back().external_id));
    }
    require(cJSON_AddItemToArray(rows.get(),row.get()),"speaker list allocation");row.release();
  }
  put(out,"speakers",std::move(rows));put(out,"used_for_permissions",boolean(false));return out;
}
Json unresolved(const char* reason) {
  auto out=object();put(out,"outcome",string("unavailable"));put(out,"reason",string(reason));
  put(out,"used_for_permissions",boolean(false));return out;
}
Json enrolled_result(const aii::uid::Decision& d,const aii::uid::Sample& sample,
    size_t count,const aii::uid::Policy& policy) {
  auto out=object();put(out,"outcome",string(d.outcome));put(out,"reason",string(d.reason));
  put(out,"speaker_id",string(d.speaker_id));put(out,"label",string(d.label));
  if(d.score)put(out,"score",own(cJSON_CreateNumber(*d.score)));
  if(d.margin)put(out,"margin",own(cJSON_CreateNumber(*d.margin)));
  put(out,"enrollment_revision",string(std::to_string(d.enrollment_revision)));
  put(out,"policy_sha256",string(d.policy_sha256));
  put(out,"embedding_binding",string(policy.embedding_binding));
  put(out,"pcm_sha256",string(sample.audio_sha256));put(out,"samples",number(count));
  put(out,"used_for_permissions",boolean(false));return out;
}
void track_evidence(Json& out,const aii::uid::Sample& sample,size_t count,const aii::uid::Policy& policy) {
  put(out,"pcm_sha256",string(sample.audio_sha256));put(out,"samples",number(count));
  put(out,"policy_sha256",string(policy.fingerprint));
  put(out,"embedding_binding",string(policy.embedding_binding));
}
// Explicit enrollment may select recordings which already formed an anonymous
// profile. Exact evidence identity, not a nearby embedding, proves those two
// gallery rows are the same source. A partial overlap is not sufficient: the
// anonymous row might include another speaker or contaminated track.
bool enrolled_contains_anonymous(const aii::uid::Snapshot& enrolled,
    const aii::uid::SpeakerRegistry& registry,const aii::uid::Decision& known,
    const aii::uid::Decision& anonymous) {
  if(known.outcome!="known"||anonymous.outcome!="known")return false;
  const auto e=std::find_if(enrolled.speakers.begin(),enrolled.speakers.end(),[&](const auto& row){
    return row.id==known.speaker_id;
  });
  const auto a=std::find_if(registry.profiles.speakers.begin(),registry.profiles.speakers.end(),[&](const auto& row){
    return row.id==anonymous.speaker_id;
  });
  if(e==enrolled.speakers.end()||a==registry.profiles.speakers.end()||a->samples.size()<2)return false;
  return std::all_of(a->samples.begin(),a->samples.end(),[&](const auto& sample){
    return std::any_of(e->samples.begin(),e->samples.end(),[&](const auto& selected){
      return selected.audio_sha256==sample.audio_sha256;
    });
  });
}
}
std::string SpeakerRegistryStore::read(bool& absent) {
  auto raw=bridge_.read(&absent,SnapshotBridge::Store::SpeakerRegistry);
  return absent?aii::uid::write_registry({0,{policy_.policy,0,{}},{}},policy_):raw;
}
const aii::uid::PolicyDocument& SpeakerRegistryStore::policy_for(const std::string& raw) const {
  // A bound predecessor is readable for management, not implicitly upgraded
  // for matching. A corrupt current document must not be reinterpreted as a
  // predecessor: both parsers still require exact canonical bytes.
  try {
    try { (void)aii::uid::read_registry(raw,policy_);return policy_; }
    catch(const std::exception&) {
      if(previous_) { (void)aii::uid::read_registry(raw,*previous_);return *previous_; }
      throw;
    }
  }
  // Incompatible and corrupt registries are reported for confirmed recovery.
  catch(const std::invalid_argument& e) { throw Refused(std::string(e.what())+"; speaker.list reports UID recovery"); }
  catch(const Refused& e) { throw Refused(std::string(e.what())+"; speaker.list reports UID recovery"); }
}
aii::uid::Snapshot SpeakerRegistryStore::read_enrolled() {
  bool absent=false;const auto raw=bridge_.read(&absent);
  if(absent)return {policy_.policy,0,{}};
  try{return aii::uid::read_snapshot(raw,policy_);}
  catch(const std::exception&) {
    if(previous_)return aii::uid::read_snapshot(raw,*previous_);
    throw;
  }
}
Json SpeakerRegistryStore::project(const std::string& raw) {
  const auto registry=aii::uid::read_registry(raw,policy_for(raw));
  if(std::any_of(registry.buckets.begin(),registry.buckets.end(),[](const auto& b){return b.enrollment.has_value();})) {
    const auto enrolled=read_enrolled();return projection(registry,&enrolled);
  }
  return projection(registry);
}
void SpeakerRegistryStore::publish(const std::string& base,const aii::uid::RegistryChange& change,bool absent) {
  if(base==change.document)return;
  auto receipt=bridge_.publish(change.document,absent?"":change.base_sha256,absent,
      picosha2::hash256_hex_string(random_uuid()),SnapshotBridge::Store::SpeakerRegistry);
  require(flag(field(receipt.get(),"durable"))&&flag(field(receipt.get(),"readback_verified")),
      "speaker registry durability unresolved");
}
void SpeakerRegistryStore::publish_observed(const std::string& base,const aii::uid::RegistryChange& change,bool absent) {
  if(base==change.document)return;
  try { publish(base,change,absent); }
  catch(const StorageLate&) {
    // Late at a stage (nothing was published) or at the publish (the outcome
    // is unknown): either way the next attempt reads the file back first.
    if(unpublished_.size()==kept_limit)unpublished_.pop_front();
    unpublished_.push_back({base,change,absent});publication_kept_=true;
    throw;
  }
}
size_t SpeakerRegistryStore::kept_observations() { std::lock_guard<std::mutex> lock(mutex_);return unobserved_.size(); }
size_t SpeakerRegistryStore::kept_publications() { std::lock_guard<std::mutex> lock(mutex_);return unpublished_.size(); }
void SpeakerRegistryStore::finish_kept() {
  // Publications first: an observation kept behind one may be about the same voice.
  while(!unpublished_.empty()) {
    const auto kept=unpublished_.front();
    try {
      bool absent=false;const auto raw=read(absent);
      if(raw!=kept.change.document && raw==kept.base && absent==kept.absent)publish(kept.base,kept.change,kept.absent);
      // Already what the change made it: it had landed. Something else: the
      // file moved on, and a change made from an older file is not applied.
    } catch(const StorageLate&) { return; } // still late: kept as it is
      catch(const std::exception&) {}       // refused for another reason: it cannot be applied later either
    unpublished_.pop_front();
  }
  while(!unobserved_.empty()) {
    const auto kept=unobserved_.front();unobserved_.pop_front();
    // The observation it was, and nothing of the utterance being observed
    // now: which tracks of that one have been given a speaker stays as it is.
    const auto track_session=track_session_,track_utterance=track_utterance_;
    const auto assigned=assigned_tracks_;
    const auto restore=[&]{track_session_=track_session;track_utterance_=track_utterance;assigned_tracks_=assigned;};
    publication_kept_=false;
    try { (void)observe_now(&kept.evidence,kept.session,kept.utterance); }
    catch(const StorageLate&) {
      restore();
      if(!publication_kept_)unobserved_.push_front(kept); // its reads were late again
      return;
    }
    catch(const std::exception&) {} // refused: observing it again would be refused again
    restore();
  }
}
Json SpeakerRegistryStore::observe(const aii_voice_capture* evidence,uint64_t session,uint64_t utterance) {
  // Identification runs on the speaker worker, not the control reader. A
  // concurrent management call must not permanently discard this utterance.
  // Management still uses try_to_lock so it cannot queue behind inference.
  std::unique_lock<std::mutex> lock(mutex_);
  publication_kept_=false;
  Json out=null();
  try { out=observe_now(evidence,session,utterance); }
  catch(const StorageLate&) {
    // The answer for this final is that storage was late (the caller says
    // so). What the files would have learned from it is kept: the change,
    // where one had been made (publish_observed), else the recording.
    if(evidence && !publication_kept_) {
      if(unobserved_.size()==kept_limit)unobserved_.pop_front();
      unobserved_.push_back({session,utterance,*evidence});
    }
    throw;
  }
  // This observation's own storage answered: what an earlier lateness left
  // undone is done now, behind the answer and never ahead of it.
  finish_kept();
  return out;
}
Json SpeakerRegistryStore::observe_now(const aii_voice_capture* evidence,uint64_t session,uint64_t utterance) {
  admissions_.expire();
  if(session!=track_session_ || utterance!=track_utterance_) {
    track_session_=session;track_utterance_=utterance;assigned_tracks_.clear();
  }
  std::optional<aii::uid::Sample> sample;
  if(evidence) {
    require(evidence->samples>=31920 && evidence->samples<=160000 && evidence->embedding_binding[64]==0 &&
      std::string(evidence->embedding_binding)==policy_.policy.embedding_binding && evidence->pcm_sha256[64]==0,
      "speaker evidence binding differs");
    aii::uid::Vector vector(evidence->embedding,evidence->embedding+aii::uid::embedding_dimensions(policy_.policy.embedding_binding));
    sample=aii::uid::Sample{evidence->pcm_sha256,vector};
  }
  const auto enrolled=sample?std::optional<aii::uid::Snapshot>(read_enrolled()):std::nullopt;
  bool absent=false;const auto raw=read(absent);
  const auto& bound=policy_for(raw);
  if(bound.policy.fingerprint!=policy_.policy.fingerprint)
    return unresolved("speaker_registry_policy_upgrade_required");
  const auto registry=aii::uid::read_registry(raw,bound);
  if(sample) {
    const auto enrolled_match=aii::uid::identify(*enrolled,enrolled->policy,sample->embedding,policy_.policy.embedding_binding);
    const auto anonymous_match=aii::uid::identify(aii::uid::anonymous_matching_profiles(registry),policy_.policy,sample->embedding,policy_.policy.embedding_binding);
    const auto margin=policy_.policy.minimum_margin;
    const bool cross_close=enrolled_match.score && anonymous_match.score &&
      std::abs(*enrolled_match.score-*anonymous_match.score)<margin;
    const auto known_result=[&](const std::string& existing=std::string()) {
      const auto chosen=std::find_if(enrolled->speakers.begin(),enrolled->speakers.end(),[&](const auto& s){return s.id==enrolled_match.speaker_id;});
      require(chosen!=enrolled->speakers.end(),"accepted enrollment missing");
      const auto change=aii::uid::bind_enrolled_speaker(raw,policy_,registry.revision,random_uuid(),*chosen,existing);
      if(session&&assigned_tracks_.count(change.uuid))return unresolved("same_speaker_on_multiple_tracks");
      // Do not attach a UUID to an enrollment replaced during host publication.
      const auto now=read_enrolled();
      const auto& enrollment_policy=enrolled->policy.fingerprint==policy_.policy.fingerprint?policy_:*previous_;
      require(now.policy.fingerprint==enrolled->policy.fingerprint &&
        aii::uid::write_snapshot(now,enrollment_policy)==aii::uid::write_snapshot(*enrolled,enrollment_policy),
        "enrollment changed during UUID binding");
      publish_observed(raw,change,absent);
      if(session)assigned_tracks_.insert(change.uuid);
      auto out=enrolled_result(enrolled_match,*sample,evidence->samples,enrolled->policy);
      put(out,"speaker_uuid",string(change.uuid));put(out,"registry_revision",string(std::to_string(change.revision)));
      put(out,"continuity",string("matched"));
      const auto mapped=aii::uid::read_registry(change.document,policy_);
      std::string label="unknown";
      for(const auto& b:mapped.buckets)if(b.uuid==change.uuid)label=label_for(b,&*enrolled);
      put(out,"display_label",string(label));
      cJSON_DeleteItemFromObject(out.get(),"label");put(out,"label",string(label));
      return out;
    };
    if(enrolled_contains_anonymous(*enrolled,registry,enrolled_match,anonymous_match)) {
      return known_result(anonymous_match.speaker_id);
    }
    // The winning identity must clear every competitor, not require the
    // losing gallery to agree with itself. Ambiguity between two weaker
    // candidates cannot veto a winner separated from both by the bound margin.
    const auto wins=[&](const aii::uid::Decision& candidate,const aii::uid::Decision& other) {
      return candidate.outcome=="known" && candidate.score &&
        (!other.score || (*candidate.score>*other.score && *candidate.score-*other.score>=margin));
    };
    if(wins(enrolled_match,anonymous_match)) {
      return known_result();
    }
    if(wins(anonymous_match,enrolled_match)) {
      const auto id=aii::uid::canonical_speaker(registry,anonymous_match.speaker_id);
      if(session && !assigned_tracks_.insert(id).second)
        return unresolved("same_speaker_on_multiple_tracks");
      auto out=unresolved("acoustic_profile_match");
      put(out,"speaker_uuid",string(id));put(out,"registry_revision",string(std::to_string(registry.revision)));
      put(out,"continuity",string("matched"));
      track_evidence(out,*sample,evidence->samples,policy_.policy);
      const auto change=aii::uid::observe_speaker(raw,policy_,registry.revision,random_uuid(),sample);
      const auto diagnostic=aii::uid::match_diagnostics(change,policy_);
      if(!diagnostic.empty()){auto match=parse(diagnostic);put(match,"evidence_samples",number(evidence->samples));put(out,"match",std::move(match));}
      std::string label="unknown";
      for(const auto& b:registry.buckets)if(b.uuid==id)label=label_for(b,&*enrolled);
      put(out,"display_label",string(label));
      return out;
    }
    if(enrolled_match.outcome=="ambiguous" || anonymous_match.outcome=="ambiguous" ||
       (cross_close && (enrolled_match.outcome=="known" || anonymous_match.outcome=="known")))
      return unresolved("cross_gallery_ambiguous");
    // A rejected but nearby enrolled match may be that same person under a
    // changed microphone or overlap. It is not positive evidence of novelty.
    if(enrolled_match.score && *enrolled_match.score>=enrolled->policy.threshold-margin)
      return unresolved("enrolled_profile_nearby");
  }
  const auto id=random_uuid();
  auto change=aii::uid::observe_speaker(raw,policy_,registry.revision,id,sample);
  if(change.reason=="speaker_profile_pending" && sample) {
    aii::uid::Sample corroborating;
    if(admissions_.corroborates(picosha2::hash256_hex_string(raw),policy_,session,utterance,
        *sample,aii::uid::ProfileAdmissions::Clock::now(),&corroborating))
      change=aii::uid::observe_speaker(raw,policy_,registry.revision,id,sample,
          aii::uid::ProfileAdmission::Corroborated,corroborating,enrolled?&*enrolled:nullptr);
  }
  if(change.document!=raw && enrolled) {
    const auto now=read_enrolled();
    const auto canonical=[&](const aii::uid::Snapshot& snapshot) {
      if(snapshot.policy.fingerprint==policy_.policy.fingerprint)
        return aii::uid::write_snapshot(snapshot,policy_);
      require(previous_ && snapshot.policy.fingerprint==previous_->policy.fingerprint,
        "enrollment policy changed during anonymous profile admission");
      return aii::uid::write_snapshot(snapshot,*previous_);
    };
    require(canonical(now)==canonical(*enrolled),
      "enrollment changed during anonymous profile admission");
  }
  publish_observed(raw,change,absent);
  auto out=object();put(out,"outcome",string("unavailable"));put(out,"reason",string(change.reason));
  if(!change.uuid.empty()) {
    if(session&&!assigned_tracks_.insert(change.uuid).second)return unresolved("same_speaker_on_multiple_tracks");
    put(out,"speaker_uuid",string(change.uuid));put(out,"registry_revision",string(std::to_string(change.revision)));
    put(out,"continuity",string(change.continuity));
    if(sample)track_evidence(out,*sample,evidence->samples,policy_.policy);
  }
  put(out,"used_for_permissions",boolean(false));
  const auto diagnostic=aii::uid::match_diagnostics(change,policy_);
  if(!diagnostic.empty()) {
    auto match=parse(diagnostic);
    put(match,"evidence_samples",number(evidence->samples));
    put(out,"match",std::move(match));
  }
  const auto next=aii::uid::read_registry(change.document,policy_);
  if(!change.uuid.empty()) {
    std::string label="unknown";
    for(const auto& b:next.buckets)if(b.uuid==change.uuid)label=label_for(b,enrolled?&*enrolled:nullptr);
    put(out,"display_label",string(label));
  }
  return out;
}
Json SpeakerRegistryStore::list() {
  std::unique_lock<std::mutex> lock(mutex_,std::try_to_lock);require(lock.owns_lock(),"speaker registry busy");
  admissions_.expire();
  bool absent=false;const auto raw=read(absent);
  auto out=project(raw);
  put(out,"policy_upgrade_required",boolean(policy_for(raw).policy.fingerprint!=policy_.policy.fingerprint));
  return out;
}
Json SpeakerRegistryStore::associate(uint64_t revision,const std::string& uuid,const std::string& label,const std::string& id) {
  std::unique_lock<std::mutex> lock(mutex_,std::try_to_lock);require(lock.owns_lock(),"speaker registry busy");
  bool absent=false;const auto raw=read(absent);
  const auto& bound=policy_for(raw);
  const auto change=aii::uid::associate_speaker(raw,bound,revision,uuid,label,id);
  publish(raw,change,absent);
  return project(change.document);
}
Json SpeakerRegistryStore::forget(uint64_t revision,const std::string& uuid) {
  std::unique_lock<std::mutex> lock(mutex_,std::try_to_lock);require(lock.owns_lock(),"speaker registry busy");
  bool absent=false;const auto raw=read(absent);
  const auto& bound=policy_for(raw);
  const auto registry=aii::uid::read_registry(raw,bound);
  for(const auto& b:registry.buckets)if(b.uuid==uuid&&b.enrollment) {
    const auto enrolled=read_enrolled();
    require(std::none_of(enrolled.speakers.begin(),enrolled.speakers.end(),[&](const auto& s){return s.id==b.enrollment->id;}),
      "remove the linked enrollment with speaker.remove before forgetting its UUID");
  }
  const auto change=aii::uid::forget_speaker(raw,bound,revision,uuid);
  publish(raw,change,absent);
  return project(change.document);
}
Json SpeakerRegistryStore::link(uint64_t revision,const std::string& source,const std::string& target) {
  std::unique_lock<std::mutex> lock(mutex_,std::try_to_lock);require(lock.owns_lock(),"speaker registry busy");
  bool absent=false;const auto raw=read(absent);
  const auto& bound=policy_for(raw);
  if(source!=target) {
    const auto registry=aii::uid::read_registry(raw,bound);
    std::optional<aii::uid::Snapshot> enrolled;
    for(const auto& id:{source,target}) {
      const auto bucket=std::find_if(registry.buckets.begin(),registry.buckets.end(),[&](const auto& b){return b.uuid==id;});
      const auto profile=std::find_if(registry.profiles.speakers.begin(),registry.profiles.speakers.end(),[&](const auto& p){return p.id==id;});
      if(profile!=registry.profiles.speakers.end())continue;
      if(!enrolled)enrolled=read_enrolled();
      require(bucket!=registry.buckets.end() && aii::uid::bound_enrollment(*bucket,*enrolled),
        "speaker linking requires two available acoustic profiles");
    }
  }
  const auto change=aii::uid::link_speaker(raw,bound,revision,source,target);
  publish(raw,change,absent);
  return project(change.document);
}
Json SpeakerRegistryStore::upgrade_policy() {
  std::unique_lock<std::mutex> lock(mutex_,std::try_to_lock);require(lock.owns_lock(),"speaker registry busy");
  bool absent=false;const auto raw=read(absent);
  const auto& bound=policy_for(raw);
  if(bound.policy.fingerprint==policy_.policy.fingerprint) {
    auto out=project(raw);
    put(out,"policy_upgrade_required",boolean(false));return out;
  }
  require(previous_&&bound.policy.fingerprint==previous_->policy.fingerprint,
      "speaker registry policy is not bound for confirmed upgrade");
  auto registry=aii::uid::read_registry(raw,bound);
  const auto old_profiles=aii::uid::write_snapshot(registry.profiles,bound);
  const auto converted=aii::uid::prepare_guided_policy_transition(old_profiles,bound,policy_);
  registry.profiles=aii::uid::read_snapshot(converted.snapshot,policy_);
  require(registry.revision<9007199254740991ULL,"speaker registry revision exhausted");
  ++registry.revision;
  require(registry.profiles.revision<=registry.revision,"speaker profile revision exceeds registry on upgrade");
  const auto candidate=aii::uid::write_registry(registry,policy_);
  publish(raw,{picosha2::hash256_hex_string(raw),candidate,"","","confirmed_policy_upgrade",registry.revision,std::nullopt},absent);
  auto out=project(candidate);
  put(out,"policy_upgrade_required",boolean(false));return out;
}
aii_voice_result SpeakerRegistryStore::callback(void* context,const aii_voice_capture* evidence,char* output,size_t capacity,size_t* written) noexcept {
  return callback_at(context,0,0,evidence,output,capacity,written);
}
aii_voice_result SpeakerRegistryStore::callback_at(void* context,uint64_t session,uint64_t utterance,const aii_voice_capture* evidence,char* output,size_t capacity,size_t* written) noexcept {
  try {
    if(!context||!output||!written||capacity<8192)return AII_VOICE_INVALID;
    *written=0;const auto result=encode(static_cast<SpeakerRegistryStore*>(context)->observe(evidence,session,utterance));
    if(result.empty()||result.size()>=capacity)return AII_VOICE_CAPACITY;
    std::memcpy(output,result.data(),result.size());*written=result.size();return AII_VOICE_OK;
  }catch(const StorageLate&){return AII_VOICE_BUSY;} // late, not unavailable (c_api.h)
  catch(...){return AII_VOICE_FAILED;}
}
}
