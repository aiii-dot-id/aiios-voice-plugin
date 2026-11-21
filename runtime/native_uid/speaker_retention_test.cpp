#include "speaker_retention.h"
#include "../native/session/worker_json.h"
#include <iostream>
#include <set>
#include <stdexcept>
using namespace aii::uid;
namespace {
void check(bool ok,const char* why){if(!ok)throw std::runtime_error(why);}
template<class F> void refuses(F f){bool failed=false;try{f();}catch(const std::exception&){failed=true;}check(failed,"invalid retention input admitted");}
const std::string a="00000000-0000-4000-8000-000000000001",b="00000000-0000-4000-8000-000000000002";
Sample sample(char hash,unsigned axis){Vector v(256);v[axis]=1;return {std::string(64,hash),v};}
RegistryChange admitted(const std::string& raw,const PolicyDocument& p,uint64_t revision,const std::string& id,const Sample& s) {
  auto support=s;support.audio_sha256.back()=support.audio_sha256.back()=='f'?'e':'f';
  return observe_speaker(raw,p,revision,id,s,ProfileAdmission::Corroborated,support);
}
void earlier_format(const std::string& raw,const PolicyDocument& p) {
  check(write_registry(read_registry(raw,p),p)==raw,"the earlier format's codec did not read the registry back to the byte");
  auto root=aii::voice::wire::parse(raw);
  std::set<std::string> top;
  for(auto* m=root->child;m;m=m->next)top.insert(m->string);
  check(top==std::set<std::string>{"schema","revision","profile_document","buckets"},"the registry gained a top-level member");
  const std::set<std::string> allowed{"uuid","created_revision","associations","enrollment","links"};
  for(auto* bucket=aii::voice::wire::field(root.get(),"buckets")->child;bucket;bucket=bucket->next)
    for(auto* m=bucket->child;m;m=m->next)check(allowed.count(m->string)>0,"a bucket gained a member the earlier format does not have");
  check(raw.find("heard")==std::string::npos,"the heard days leaked into the registry");
}
}
int main(){try{
  const auto p=read_policy("{\"calibration_sha256\":\""+std::string(64,'a')+"\",\"embedding_binding\":\""+std::string(64,'b')+
    "\",\"minimum_enrollment_samples\":1,\"minimum_margin\":0.105,\"threshold\":0.56}");
  const auto empty=write_registry({0,{p.policy,0,{}},{}},p);
  const uint64_t day=20000;
  const auto stranger=admitted(empty,p,0,a,sample('1',0));
  check(stranger.uuid==a,"fixture did not make a stranger's voiceprint");
  earlier_format(stranger.document,p);
  const auto first=expire_unenrolled(stranger.document,p,{},day,30);
  check(first.registry.document==stranger.document&&first.expired.empty()&&first.heard==HeardDays{{a,day}},"first sight deleted or did not start the count");
  for(const uint32_t days:{30u,7u}) {
    const auto kept=expire_unenrolled(stranger.document,p,first.heard,day+days-1,days);
    check(kept.registry.document==stranger.document&&kept.expired.empty(),"a voiceprint expired before its period");
    const auto gone=expire_unenrolled(stranger.document,p,first.heard,day+days,days);
    const auto after=read_registry(gone.registry.document,p);
    check(gone.expired==std::vector<std::string>{a}&&after.buckets.empty()&&after.profiles.speakers.empty()&&
      after.revision==2&&gone.heard.empty(),"a never-enrolled voiceprint outlived its period, or its day was kept");
    earlier_format(gone.registry.document,p);
  }
  const auto registry=read_registry(stranger.document,p);
  check(expiry_day(registry,registry.buckets[0],first.heard,30)==day+30&&!expiry_day(registry,registry.buckets[0],{},30),"expiry day misreported");
  refuses([&]{expire_unenrolled(stranger.document,p,first.heard,day,0);});
  const auto named=associate_speaker(stranger.document,p,1,a,"Chosen label","");
  check(expire_unenrolled(named.document,p,first.heard,day+30,30).expired==std::vector<std::string>{a},"a name kept a never-enrolled voiceprint");
  auto again=heard_speaker(registry,first.heard,a,day+20);
  check(again==HeardDays{{a,day+20}}&&heard_speaker(registry,again,a,day+5)==again,"a match did not move the day forward only");
  check(expire_unenrolled(stranger.document,p,again,day+30,30).expired.empty()&&
    expire_unenrolled(stranger.document,p,again,day+50,30).expired==std::vector<std::string>{a},"a returning voice expired from its first day");
  Speaker person{"enrolled-id","Enrolled person",{sample('c',1)}};
  const auto bound=bind_enrolled_speaker(stranger.document,p,1,b,person);
  const Snapshot present{p.policy,1,{person}},removed{p.policy,2,{}};
  const auto kept=expire_unenrolled(bound.document,p,first.heard,day+1000,30,&present);
  const auto kept_registry=read_registry(kept.registry.document,p);
  check(kept.expired==std::vector<std::string>{a}&&kept_registry.buckets.size()==1&&kept_registry.buckets[0].uuid==b&&
    kept.heard.empty(),"an enrolled person's UUID expired, or was given a day, or the stranger beside it was kept");
  earlier_format(kept.registry.document,p);
  check(expire_unenrolled(bound.document,p,first.heard,day+1000,30).expired==std::vector<std::string>{a},"an unread enrollment did not keep its UUID");
  check(heard_speaker(read_registry(bound.document,p),{},b,day,&present).empty(),"an enrolled UUID was given a day");
  const auto unbound=expire_unenrolled(bound.document,p,first.heard,day+1000,30,&removed);
  check(unbound.heard.at(b)==day+1000,"a removed enrollment's UUID was not given its day");
  check(expire_unenrolled(unbound.registry.document,p,unbound.heard,day+1030,30,&removed).expired==std::vector<std::string>{b},
    "a removed enrollment's UUID was kept for ever");
  check(expire_unenrolled(empty,p,HeardDays{{a,day}},day,30).heard.empty(),"a row outlived its bucket");
  const auto pair=admitted(stranger.document,p,1,b,sample('2',1));
  const auto joined=link_speaker(pair.document,p,2,b,a);
  const HeardDays both{{a,day},{b,day+10}};
  check(expire_unenrolled(joined.document,p,both,day+30,30).expired.empty(),"a link group expired from its older member's day");
  const auto together=expire_unenrolled(joined.document,p,both,day+40,30);
  check((together.expired==std::vector<std::string>{a,b})&&read_registry(together.registry.document,p).buckets.empty(),"a link group did not expire together");
  earlier_format(together.registry.document,p);
  const auto text=write_heard(both);
  check(text=="{\"schema\":1,\"heard\":[{\"day\":20000,\"uuid\":\""+a+"\"},{\"day\":20010,\"uuid\":\""+b+"\"}]}"&&read_heard(text)==both,"heard days not stored canonically");
  for(const std::string& bad:std::vector<std::string>{"{}","{\"schema\":2,\"heard\":[]}","{\"schema\":1,\"heard\":[{\"day\":\"1\",\"uuid\":\""+a+"\"}]}",
      "{\"schema\":1,\"heard\":[{\"day\":1,\"uuid\":\"track-0\"}]}","{\"schema\":1,\"heard\":[{\"day\":1,\"uuid\":\""+a+"\",\"label\":\"x\"}]}",
      text+" ","{\"schema\":1,\"heard\":[{\"day\":20010,\"uuid\":\""+b+"\"},{\"day\":20000,\"uuid\":\""+a+"\"}]}"})
    refuses([&]{read_heard(bad);});
  std::cout<<"voiceprint expiry from a separate heard-days file; the registry stays in the earlier format\n";
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
