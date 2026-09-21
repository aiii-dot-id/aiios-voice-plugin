#include "speaker_registry.h"
#include <cmath>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <stdexcept>
using namespace aii::uid;
void check(bool ok,const char* why){if(!ok)throw std::runtime_error(why);}
template<class F> void refuses(F f){bool failed=false;try{f();}catch(const std::exception&){failed=true;}check(failed,"invalid registry action admitted");}
const std::string a="00000000-0000-4000-8000-000000000001",b="00000000-0000-4000-8000-000000000002",c="00000000-0000-4000-8000-000000000003";
Sample sample(char hash,unsigned axis){Vector v{};v[axis]=1;return {std::string(64,hash),v};}
int main(int argc,char** argv){try{
  const auto p=read_policy("{\"calibration_sha256\":\""+std::string(64,'a')+"\",\"embedding_binding\":\""+std::string(64,'b')+
    "\",\"minimum_enrollment_samples\":1,\"minimum_margin\":0.105,\"threshold\":0.56}");
  const auto empty=write_registry({0,{p.policy,0,{}},{}},p);
  if(argc==3&&std::string(argv[1])=="reload") {
    std::ifstream f(argv[2]);std::string bytes{std::istreambuf_iterator<char>(f),{}};
    auto r=read_registry(bytes,p);check(r.buckets.size()==2,"restart bucket count");
    auto match=observe_speaker(bytes,p,r.revision,c,sample('3',0));
    check(match.uuid==a&&match.continuity=="matched"&&match.document==bytes,"restart acoustic continuity");
    auto named=associate_speaker(bytes,p,r.revision,a,"New label","external-person-1");
    check(read_registry(named.document,p).buckets[0].associations.size()==3,"after restart naming/history");return 0;
  }
  auto first=observe_speaker(empty,p,0,a,sample('1',0));check(first.uuid==a&&first.continuity=="new_profile","first anonymous profile");
  auto second=observe_speaker(first.document,p,1,b,sample('2',1));check(second.uuid==b,"distinct voice merged");
  auto again=observe_speaker(second.document,p,2,c,sample('3',0));
  check(again.uuid==a&&again.continuity=="matched"&&again.document==second.document,"returning voice not reused read-only");
  auto named=associate_speaker(second.document,p,2,a,"Chosen label","");
  auto r=read_registry(named.document,p);check(r.revision==3&&r.profiles.speakers[0].label==a,"label rewrote acoustic identity");
  check(r.buckets[0].associations.back().label=="Chosen label","association missing");
  check(associate_speaker(named.document,p,3,a,"Chosen label","").document==named.document,"duplicate association advanced revision");
  refuses([&]{associate_speaker(named.document,p,2,a,"Stale","");});
  auto renamed=associate_speaker(named.document,p,3,a,"Corrected label","");
  r=read_registry(renamed.document,p);check(r.buckets[0].associations.size()==2&&r.buckets[0].associations[0].label=="Chosen label","old association lost");
  auto cleared=associate_speaker(renamed.document,p,4,a,"","");check(read_registry(cleared.document,p).buckets[0].associations.back().label.empty(),"clear association failed");
  auto provisional=observe_speaker(empty,p,0,a,std::nullopt);
  check(read_registry(provisional.document,p).profiles.speakers.empty()&&provisional.continuity=="provisional","missing evidence invented profile");
  auto ambiguous=read_registry(second.document,p);ambiguous.profiles.speakers[1].samples[0].embedding=sample('2',0).embedding;
  auto amb=observe_speaker(write_registry(ambiguous,p),p,2,c,sample('3',0));
  check(amb.uuid==c&&amb.continuity=="provisional"&&amb.reason=="ambiguous_profile_match","ambiguous matched a person");
  check(read_registry(amb.document,p).profiles.speakers.size()==2,"ambiguous contaminated profiles");
  refuses([&]{observe_speaker(empty,p,0,"named-person",sample('1',0));});
  refuses([&]{observe_speaker(first.document,p,1,a,sample('3',0));});
  refuses([&]{observe_speaker(empty,p,1,a,sample('1',0));});
  refuses([&]{associate_speaker(first.document,p,1,b,"Label","");});
  refuses([&]{associate_speaker(first.document,p,1,a,"bad\nlabel","");});
  refuses([&]{observe_speaker(first.document,p,1,c,sample('z',0));});
  auto invalid=sample('3',0);invalid.embedding[0]=NAN;refuses([&]{observe_speaker(first.document,p,1,c,invalid);});
  invalid=sample('3',0);invalid.embedding[0]=2;refuses([&]{observe_speaker(first.document,p,1,c,invalid);});
  refuses([&]{read_registry("{}",p);});refuses([&]{read_registry(first.document+" ",p);});
  auto different=p;different.policy.embedding_binding=std::string(64,'c');refuses([&]{read_registry(first.document,different);});
  auto exhausted=read_registry(first.document,p);exhausted.revision=9007199254740991ULL;
  refuses([&]{associate_speaker(write_registry(exhausted,p),p,exhausted.revision,a,"Label","");});
  auto saturated=SpeakerRegistry{256,{p.policy,0,{}},{}};
  for(unsigned i=1;i<=256;++i){char id[37]{};std::snprintf(id,sizeof id,"00000000-0000-4000-8000-%012x",i);saturated.buckets.push_back({id,i,{}});}
  refuses([&]{observe_speaker(write_registry(saturated,p),p,256,"ffffffff-ffff-4fff-8fff-ffffffffffff",std::nullopt);});
  const auto full=write_registry(saturated,p);
  const auto freed=forget_speaker(full,p,256,saturated.buckets.front().uuid);
  check(read_registry(freed.document,p).buckets.size()==255 && freed.revision==257,"forget failed to release capacity");
  const auto resumed=observe_speaker(freed.document,p,257,"ffffffff-ffff-4fff-8fff-ffffffffffff",std::nullopt);
  check(read_registry(resumed.document,p).buckets.size()==256,"registry did not recover after explicit retention");
  refuses([&]{forget_speaker(full,p,255,saturated.buckets.front().uuid);});
  const auto removed=forget_speaker(first.document,p,1,a);
  check(read_registry(removed.document,p).profiles.speakers.empty(),"forgotten profile retained");
  refuses([&]{forget_speaker(removed.document,p,removed.revision,a);});
  auto history=read_registry(provisional.document,p);
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
