#include "speaker_registry.h"
#include "../native/session/worker_json.h"
#include <cmath>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <stdexcept>
using namespace aii::uid;
void check(bool ok,const char* why){if(!ok)throw std::runtime_error(why);}
template<class F> void refuses(F f){bool failed=false;try{f();}catch(const std::exception&){failed=true;}check(failed,"invalid registry action admitted");}
const std::string a="00000000-0000-4000-8000-000000000001",b="00000000-0000-4000-8000-000000000002",c="00000000-0000-4000-8000-000000000003";
Sample sample(char hash,unsigned axis){Vector v(256);v[axis]=1;return {std::string(64,hash),v};}
// Codec/mutation fixtures explicitly exercise already-corroborated admission.
// The default unknown path is tested independently below and in the store.
RegistryChange admitted(const std::string& raw,const PolicyDocument& p,uint64_t revision,
    const std::string& id,const std::optional<Sample>& sample) {
  if(!sample)return observe_speaker(raw,p,revision,id,sample);
  auto support=*sample;
  support.audio_sha256.back()=support.audio_sha256.back()=='f'?'e':'f';
  return observe_speaker(raw,p,revision,id,sample,ProfileAdmission::Corroborated,support);
}
int main(int argc,char** argv){try{
  const auto empty_association=aii::voice::wire::parse(R"({"display_label":"","external_id":""})");
  check(aii::voice::wire::bounded_text(aii::voice::wire::field(empty_association.get(),"display_label"),512).empty()&&
        aii::voice::wire::bounded_text(aii::voice::wire::field(empty_association.get(),"external_id"),512).empty(),
        "empty metadata could not reach the association clear operation");
  const auto p=read_policy("{\"calibration_sha256\":\""+std::string(64,'a')+"\",\"embedding_binding\":\""+std::string(64,'b')+
    "\",\"minimum_enrollment_samples\":1,\"minimum_margin\":0.105,\"threshold\":0.56}");
  const auto empty=write_registry({0,{p.policy,0,{}},{}},p);
  {
    Speaker speaker{"legacy-id","Original label",{sample('a',0)}};
    const auto bound=bind_enrolled_speaker(empty,p,0,a,speaker);
    check(bound.uuid==a&&bound.revision==1,"enrollment did not gain persisted UUID");
    const auto loaded=read_registry(bound.document,p);
    check(loaded.profiles.speakers.empty()&&loaded.buckets[0].enrollment->id==speaker.id,
      "enrollment binding duplicated acoustic profiles");
    speaker.label="Renamed";speaker.samples.push_back(sample('b',0));
    const auto again=bind_enrolled_speaker(bound.document,p,1,b,speaker);
    check(again.uuid==a&&again.document==bound.document,"rename/additional evidence changed UUID");
    auto replaced=speaker;replaced.samples={sample('c',1)};
    refuses([&]{bind_enrolled_speaker(bound.document,p,1,b,replaced);});
    const auto other=bind_enrolled_speaker(bound.document,p,1,b,Speaker{"other-id","Renamed",{sample('d',1)}});
    check(other.uuid==b,"same display label collapsed different people");
    auto invalid=loaded;invalid.buckets[0].enrollment->evidence.clear();
    refuses([&]{write_registry(invalid,p);});
    invalid=loaded;invalid.buckets.push_back({b,2,{},{},loaded.buckets[0].enrollment});invalid.revision=2;
    refuses([&]{write_registry(invalid,p);});
    const auto anonymous=admitted(empty,p,0,b,sample('1',0));
    check(associate_speaker(anonymous.document,p,1,b,"","").document==anonymous.document,
      "first empty anonymous association stopped being a no-op");
    const auto anonymous_registry=read_registry(anonymous.document,p);
    auto exact=Speaker{"new-name","Context label",anonymous_registry.profiles.speakers[0].samples};
    const auto named=bind_enrolled_speaker(anonymous.document,p,1,c,exact,b);
    check(named.uuid==b&&read_registry(named.document,p).buckets.size()==1,
      "naming an anonymous voice minted a replacement UUID");
    refuses([&]{bind_enrolled_speaker(anonymous.document,p,1,c,speaker,b);});
  }
  const auto pending=observe_speaker(empty,p,0,a,sample('1',0));
  check(pending.document==empty&&pending.uuid.empty()&&pending.reason=="speaker_profile_pending",
    "one unknown observation minted a permanent person");
  {
    SpeakerRegistry legacy{2,{p.policy,1,{{a,a,{sample('1',0)}}}},
      {{a,1,{{2,"Retained label","retained-relationship"}}, {}}}};
    const auto raw=write_registry(legacy,p);
    const auto observed=admitted(raw,p,2,b,sample('2',0));
    check(observed.uuid.empty()&&observed.document==raw&&observed.match&&
      observed.match->outcome=="unknown"&&observed.match->candidate_id==a&&
      observed.reason=="anonymous_profile_needs_corroboration",
      "legacy singleton became accepted or was replaced by a new UUID");
    check(anonymous_matching_profiles(read_registry(raw,p)).speakers.empty(),
      "restart promoted an uncorroborated legacy singleton");
    auto near=sample('3',0);near.embedding[0]=.5;near.embedding[1]=std::sqrt(.75);
    const auto held=admitted(raw,p,2,b,near);
    check(held.uuid.empty()&&held.document==raw&&held.reason=="corroborated_profile_near_existing",
      "excluding legacy recognition removed its duplicate-admission guard");
    auto corrupt=legacy;corrupt.profiles.speakers[0].samples[0].embedding[0]=0;
    refuses([&]{anonymous_matching_profiles(corrupt);});
  }
  if(argc==3&&std::string(argv[1])=="reload") {
    std::ifstream f(argv[2]);std::string bytes{std::istreambuf_iterator<char>(f),{}};
    auto r=read_registry(bytes,p);check(r.buckets.size()==2,"restart bucket count");
    auto match=admitted(bytes,p,r.revision,c,sample('3',0));
    check(match.uuid==a&&match.continuity=="matched"&&match.document==bytes,"restart acoustic continuity");
    auto named=associate_speaker(bytes,p,r.revision,a,"New label","external-person-1");
    check(read_registry(named.document,p).buckets[0].associations.size()==3,"after restart naming/history");return 0;
  }
  auto first=admitted(empty,p,0,a,sample('1',0));check(first.uuid==a&&first.continuity=="new_profile","first anonymous profile");
  check(read_registry(first.document,p).profiles.speakers.at(0).samples.size()==2,
    "corroborated profile discarded its first recording");
  auto support=sample('7',0),challenger=sample('8',0);
  support.embedding=Vector(256);challenger.embedding=Vector(256);
  const double side=std::sqrt(1-.55*.55);
  support.embedding[0]=challenger.embedding[0]=.55;
  support.embedding[1]=side;
  challenger.embedding[1]=side*.7;
  challenger.embedding[2]=side*std::sqrt(1-.7*.7);
  const auto withheld=observe_speaker(first.document,p,1,b,challenger,
    ProfileAdmission::Corroborated,support);
  check(withheld.document==first.document&&withheld.uuid.empty()&&
    withheld.reason=="corroborated_profile_conflicts_with_existing"&&withheld.match&&
    withheld.match->outcome=="unknown"&&std::abs(*withheld.match->score-.55)<1e-12,
    "two rejected utterances averaged into an existing profile and minted a duplicate");
  refuses([&]{observe_speaker(empty,p,0,b,sample('7',0),
    ProfileAdmission::Corroborated,sample('8',1));});
  check(first.match&&first.match->reason=="no_enrollments"&&!first.match->score,
    "empty registry fabricated a match score");
  // A rejected nearest candidate must remain inspectable without becoming an
  // accepted identity. Synthetic unit vectors, not a person's biometric data.
  auto changed_channel=sample('4',0);changed_channel.embedding[0]=.46;
  changed_channel.embedding[1]=std::sqrt(1-.46*.46);
  const auto near_split=admitted(first.document,p,1,b,changed_channel);
  check(near_split.uuid.empty()&&near_split.document==first.document&&
        near_split.reason=="corroborated_profile_near_existing"&&near_split.match&&
        near_split.match->reason=="below_acceptance_threshold",
        "condition-shifted speaker minted a second durable UUID");
  changed_channel.embedding[0]=.30;
  changed_channel.embedding[1]=std::sqrt(1-.30*.30);
  Snapshot enrolled{p.policy,1,{{"enrolled","Known person",{sample('a',1)}}}};
  auto enrolled_support=changed_channel;enrolled_support.audio_sha256=std::string(64,'e');
  const auto enrolled_duplicate=observe_speaker(first.document,p,1,b,changed_channel,
    ProfileAdmission::Corroborated,enrolled_support,&enrolled);
  check(enrolled_duplicate.uuid.empty()&&enrolled_duplicate.document==first.document&&
        enrolled_duplicate.reason=="corroborated_profile_near_existing",
        "enrolled speaker admitted as an anonymous durable UUID");
  const auto split=admitted(first.document,p,1,b,changed_channel);
  check(split.match&&split.match->candidate_id==a&&split.match->speaker_id.empty()&&
    split.match->reason=="below_acceptance_threshold"&&std::abs(*split.match->score-.30)<1e-12&&
    split.match->enrollment_revision==1&&split.continuity=="new_profile",
    "rejected acoustic candidate lost diagnostics or claimed identity");
  check(match_diagnostics(split,p).find("embedding_f64le_b64")==std::string::npos,"diagnostics exposed embeddings");
  // A confirmed correction joins output identities, NOT their embeddings or
  // decision regions. An explicit link can still correct clearly separated profiles.
  const auto linked=link_speaker(split.document,p,2,b,a);
  const auto linked_registry=read_registry(linked.document,p);
  check(write_snapshot(linked_registry.profiles,p)==write_snapshot(read_registry(split.document,p).profiles,p),
    "link changed acoustic profiles or profile revision");
  auto query=changed_channel;query.audio_sha256=std::string(64,'5');
  const auto corrected=admitted(linked.document,p,3,c,query);
  check(corrected.uuid==a&&corrected.continuity=="matched"&&corrected.match->candidate_id==b&&
    corrected.document==linked.document,"confirmed correction failed or adapted a profile");
  check(admitted(linked.document,p,3,c,sample('6',0)).uuid==a,"original voice lost after correction");
  // Grouped best-exemplar matching failed open-set evaluation. Until its
  // replacement is qualified, linking must not relax the acoustic decision.
  auto between=sample('7',0);
  for(size_t i=0;i<256;++i)between.embedding[i]=(sample('1',0).embedding[i]+changed_channel.embedding[i])/std::sqrt(2+2*.30);
  const auto uncertain=admitted(linked.document,p,3,c,between);
  check(uncertain.match->outcome=="ambiguous"&&uncertain.uuid.empty()&&uncertain.document==linked.document,
    "ambiguous linked exemplars mutated the registry or guessed identity");
  auto competitor=linked_registry;competitor.revision=4;competitor.profiles.revision=4;
  competitor.buckets.push_back({c,4,{},{}});
  competitor.profiles.speakers.push_back({c,c,{sample('9',0),sample('a',0)}});
  const auto competing=write_registry(competitor,p);
  const auto collision_match=admitted(competing,p,4,"00000000-0000-4000-8000-000000000004",sample('8',0));
  check(collision_match.match->candidate_count==3&&collision_match.match->outcome=="ambiguous"&&
    collision_match.match->margin==0&&collision_match.uuid.empty()&&collision_match.document==competing,
    "confirmed group bypassed a different identity's ambiguity fence");
  for(unsigned i=0;i<64;++i) {
    auto q=sample('8',0);const double angle=i*.1;
    q.embedding[0]=std::cos(angle);q.embedding[1]=std::sin(angle);
    const auto before=admitted(split.document,p,2,c,q);
    const auto after=admitted(linked.document,p,3,c,q);
    check(match_diagnostics(before,p)==match_diagnostics(after,p)&&before.continuity==after.continuity,
      "confirmed correction changed an acoustic acceptance or rejection");
    check(after.uuid==(before.continuity=="matched"?a:before.uuid),"correction guessed identity for an unaccepted query");
  }
  check(link_speaker(linked.document,p,3,b,a).document==linked.document,"idempotent link changed revision");
  refuses([&]{link_speaker(linked.document,p,2,b,a);});
  refuses([&]{link_speaker(linked.document,p,3,a,b);});
  refuses([&]{forget_speaker(linked.document,p,3,a);});
  refuses([&]{link_speaker(linked.document,p,3,c,a);});
  const auto unlinked=link_speaker(linked.document,p,3,b,b);
  check(read_registry(unlinked.document,p).buckets[1].links.size()==2&&
    admitted(unlinked.document,p,4,c,query).uuid==b,"undo lost history or failed to restore identity");
  auto bad_link=linked_registry;bad_link.buckets[0].links.push_back({4,b});bad_link.revision=4;
  refuses([&]{write_registry(bad_link,p);});
  bad_link=linked_registry;bad_link.buckets[1].links.back().second=c;
  refuses([&]{write_registry(bad_link,p);});
  const auto diagnostic=aii::voice::wire::parse(match_diagnostics(split,p));
  check(std::abs(aii::voice::wire::field(diagnostic.get(),"score")->valuedouble-.30)<1e-12&&
    std::abs(aii::voice::wire::field(diagnostic.get(),"threshold")->valuedouble-.56)<1e-12,
    "serialized diagnostic truncated fractional score/threshold");
  auto second=admitted(first.document,p,1,b,sample('2',1));check(second.uuid==b,"distinct voice merged");
  auto again=admitted(second.document,p,2,c,sample('3',0));
  check(again.uuid==a&&again.continuity=="matched"&&again.document==second.document,"returning voice not reused read-only");
  check(again.match&&again.match->candidate_id==a&&again.match->candidate_count==2&&
    again.match->score==1&&again.match->margin==1,"match discarded actual decision metrics");
  auto named=associate_speaker(second.document,p,2,a,"Chosen label","");
  auto r=read_registry(named.document,p);check(r.revision==3&&r.profiles.speakers[0].label==a,"label rewrote acoustic identity");
  check(r.buckets[0].associations.back().label=="Chosen label","association missing");
  check(associate_speaker(named.document,p,3,a,"Chosen label","").document==named.document,"duplicate association advanced revision");
  refuses([&]{associate_speaker(named.document,p,2,a,"Stale","");});
  auto renamed=associate_speaker(named.document,p,3,a,"Corrected label","");
  r=read_registry(renamed.document,p);check(r.buckets[0].associations.size()==2&&r.buckets[0].associations[0].label=="Chosen label","old association lost");
  auto cleared=associate_speaker(renamed.document,p,4,a,"","");check(read_registry(cleared.document,p).buckets[0].associations.back().label.empty(),"clear association failed");
  auto provisional=admitted(empty,p,0,a,std::nullopt);
  check(provisional.uuid.empty()&&provisional.document==empty&&provisional.revision==0&&
    provisional.continuity=="provisional","missing evidence mutated durable identities");
  check(!provisional.match&&match_diagnostics(provisional,p).empty(),"missing evidence invented diagnostics");
  // Previously persisted provisional rows remain readable/nameable. No silent
  // cleanup or reinterpretation of another user's references.
  auto old=read_registry(empty,p);old.revision=1;old.buckets.push_back({a,1,{},{}});
  const auto old_bytes=write_registry(old,p);
  check(admitted(old_bytes,p,1,b,std::nullopt).document==old_bytes,"old unresolved row deleted");
  check(read_registry(associate_speaker(old_bytes,p,1,a,"Observed voice","").document,p)
    .buckets[0].associations.back().label=="Observed voice","old provisional cannot be named");
  auto ambiguous=read_registry(second.document,p);
  for(auto& member:ambiguous.profiles.speakers[1].samples)member.embedding=sample('2',0).embedding;
  auto amb=admitted(write_registry(ambiguous,p),p,2,c,sample('3',0));
  check(amb.uuid.empty()&&amb.document==write_registry(ambiguous,p)&&amb.revision==2&&
    amb.continuity=="provisional"&&amb.reason=="ambiguous_profile_match","ambiguous mutated identities");
  check(read_registry(amb.document,p).profiles.speakers.size()==2,"ambiguous contaminated profiles");
  check(amb.match&&amb.match->reason=="insufficient_separation"&&amb.match->margin==0,
    "ambiguous candidate diagnostics lost");
  refuses([&]{admitted(empty,p,0,"named-person",sample('1',0));});
  refuses([&]{admitted(first.document,p,1,a,sample('3',0));});
  refuses([&]{admitted(empty,p,1,a,sample('1',0));});
  refuses([&]{associate_speaker(first.document,p,1,b,"Label","");});
  refuses([&]{associate_speaker(first.document,p,1,a,"bad\nlabel","");});
  refuses([&]{admitted(first.document,p,1,c,sample('z',0));});
  auto invalid=sample('3',0);invalid.embedding[0]=NAN;refuses([&]{admitted(first.document,p,1,c,invalid);});
  invalid=sample('3',0);invalid.embedding[0]=2;refuses([&]{admitted(first.document,p,1,c,invalid);});
  refuses([&]{read_registry("{}",p);});refuses([&]{read_registry(first.document+" ",p);});
  auto different=p;different.policy.embedding_binding=std::string(64,'c');refuses([&]{read_registry(first.document,different);});
  auto exhausted=read_registry(first.document,p);exhausted.revision=9007199254740991ULL;
  refuses([&]{associate_speaker(write_registry(exhausted,p),p,exhausted.revision,a,"Label","");});
  auto saturated=SpeakerRegistry{256,{p.policy,0,{}},{}};
  for(unsigned i=1;i<=256;++i){char id[37]{};std::snprintf(id,sizeof id,"00000000-0000-4000-8000-%012x",i);saturated.buckets.push_back({id,i,{}});}
  const auto full=write_registry(saturated,p);
  for(unsigned i=0;i<1000;++i)check(admitted(full,p,256,
    "ffffffff-ffff-4fff-8fff-ffffffffffff",std::nullopt).document==full,"unresolved speech exhausts registry");
  refuses([&]{admitted(full,p,256,"ffffffff-ffff-4fff-8fff-ffffffffffff",sample('3',0));});
  const auto freed=forget_speaker(full,p,256,saturated.buckets.front().uuid);
  check(read_registry(freed.document,p).buckets.size()==255 && freed.revision==257,"forget failed to release capacity");
  const auto resumed=admitted(freed.document,p,257,"ffffffff-ffff-4fff-8fff-ffffffffffff",sample('3',0));
  check(read_registry(resumed.document,p).buckets.size()==256,"registry did not recover after explicit retention");
  refuses([&]{forget_speaker(full,p,255,saturated.buckets.front().uuid);});
  const auto removed=forget_speaker(first.document,p,1,a);
  check(read_registry(removed.document,p).profiles.speakers.empty(),"forgotten profile retained");
  refuses([&]{forget_speaker(removed.document,p,removed.revision,a);});
  auto history=read_registry(first.document,p);
  for(unsigned i=0;i<1024;++i)history.buckets[0].associations.push_back({i+2,"Label "+std::to_string(i),""});
  history.revision=1025;
  refuses([&]{associate_speaker(write_registry(history,p),p,1025,a,"One more","");});
  auto collision=read_registry(second.document,p);collision.buckets[1].created_revision=1;
  refuses([&]{write_registry(collision,p);});
  const auto unit_policy=read_policy("{\"calibration_sha256\":\""+std::string(64,'a')+"\",\"embedding_binding\":\""+std::string(64,'b')+
    "\",\"minimum_enrollment_samples\":1,\"minimum_margin\":0.0,\"threshold\":1.0}");
  check(read_registry(write_registry({0,{unit_policy.policy,0,{}},{}},unit_policy),unit_policy).revision==0,"canonical policy float changed");
  if(argc==3&&std::string(argv[1])=="save") {std::ofstream f(argv[2],std::ios::binary);f<<renamed.document;f.close();check(bool(f),"save failed");}
  std::cout<<"anonymous registry continuity, ambiguity, naming, revisions, codec and bounds passed\n";
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
