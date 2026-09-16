#include "enrollment.h"
#include "../native/vendor/picosha2/picosha2.h"
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
using namespace aii::uid;
void check(bool ok,const char* reason){if(!ok)throw std::runtime_error(reason);}
template<class F> void refused(F fn,const char* reason){bool failed=false;try{fn();}catch(const std::invalid_argument&){failed=true;}catch(const std::runtime_error&){failed=true;}check(failed,reason);}
Sample sample(char hash,unsigned axis=0,double value=1){Vector v{};v[axis]=value;return {std::string(64,hash),v};}
int main(){try {
  const auto policy=read_policy("{\"calibration_sha256\":\""+std::string(64,'a')+"\",\"embedding_binding\":\""+std::string(64,'b')+
      "\",\"minimum_enrollment_samples\":3,\"minimum_margin\":0.105,\"threshold\":0.56}");
  const auto empty=write_snapshot({policy.policy,0,{}},policy);
  auto enroll=[&](const std::string& base,const std::string& id,const std::string& label,std::vector<Sample> samples){
    return prepare_enrollment(base,policy,id,label,samples,policy.policy.embedding_binding);};
  const auto first=enroll(empty,"speaker","Operator-selected label",{sample('3'),sample('1',0,2),sample('2')});
  check(first.revision==1&&first.base_sha256==picosha2::hash256_hex_string(empty),"batch base or revision differs");
  auto snap=read_snapshot(first.snapshot,policy);
  check(snap.speakers.size()==1&&snap.speakers[0].samples.size()==3&&snap.speakers[0].samples[0].audio_sha256==std::string(64,'1'),"whole sorted batch missing");
  Vector query{};query[0]=1;
  check(identify(snap,policy.policy,query,policy.policy.embedding_binding).outcome=="known","prepared enrollment is not usable");
  check(read_snapshot(empty,policy).speakers.empty(),"preparation mutated its input");
  check(enroll(empty,"speaker","Operator-selected label",{sample('1',0,2),sample('2'),sample('3')}).snapshot==first.snapshot,"input order changed canonical snapshot");
  const auto collecting=enroll(empty,"one","Collecting",{sample('1')});
  check(identify(read_snapshot(collecting.snapshot,policy),policy.policy,query,policy.policy.embedding_binding).reason=="insufficient_enrollment","one sample pretended to be ready");
  refused([&]{enroll("", "one","Label",{sample('1')});},"missing snapshot became empty");
  refused([&]{enroll("{}", "one","Label",{sample('1')});},"invalid snapshot became empty");
  refused([&]{enroll(first.snapshot,"speaker","Renamed",{sample('4')});},"silent identity replacement");
  refused([&]{enroll(first.snapshot,"other","Other",{sample('1')});},"same audio enrolled as a different person");
  refused([&]{enroll(empty,"one","Label",{sample('1'),sample('1')});},"duplicate evidence counted twice");
  refused([&]{enroll(empty,"one","Label",{sample('1'),sample('2',0,-1)});},"unusable centroid accepted");
  refused([&]{enroll(empty,"one","bad\nlabel",{sample('1')});},"control character in label");
  refused([&]{enroll(empty,"../one","Label",{sample('1')});},"invalid ID accepted");
  refused([&]{enroll(empty,"one","Label",{sample('z')});},"invalid audio digest accepted");
  refused([&]{enroll(empty,"one","Label",{sample('1',0,std::numeric_limits<double>::quiet_NaN())});},"NaN accepted");
  refused([&]{enroll(empty,"one","Label",{sample('1',0,0)});},"zero embedding accepted");
  refused([&]{prepare_enrollment(empty,policy,"one","Label",{sample('1')},std::string(64,'c'));},"different model accepted");
  const auto unchanged=first.snapshot;
  refused([&]{enroll(first.snapshot,"speaker","Operator-selected label",{sample('4'),sample('5',0,0)});},"invalid second recording accepted");
  check(first.snapshot==unchanged,"partial batch escaped failure");
  const auto full=enroll(first.snapshot,"speaker","Operator-selected label",{sample('4'),sample('5'),sample('6'),sample('7'),sample('8')});
  refused([&]{enroll(full.snapshot,"speaker","Operator-selected label",{sample('9')});},"ninth recording accepted");
  auto saturated=Snapshot{policy.policy,1,{}};
  for(int i=0;i<256;++i){auto s=sample('a');s.audio_sha256=std::string(61,'a')+"000";for(int j=0;j<3;++j)s.audio_sha256[63-j]="0123456789abcdef"[(i>>(j*4))&15];saturated.speakers.push_back({"s"+s.audio_sha256,"Label",{s}});}
  const auto max=write_snapshot(saturated,policy);
  refused([&]{enroll(max,"z","Label",{sample('1')});},"257th speaker accepted");
  const auto absent=prepare_removal(first.snapshot,policy,"absent");
  check(absent.snapshot==first.snapshot&&absent.revision==1,"absent removal changed revision");
  const auto removed=prepare_removal(first.snapshot,policy,"speaker");
  check(removed.revision==2&&read_snapshot(removed.snapshot,policy).speakers.empty(),"removal failed");
  const auto reset=prepare_reset(first.snapshot,policy);
  check(reset.revision==2&&read_snapshot(reset.snapshot,policy).speakers.empty(),"reset failed");
  auto exhausted=snap;exhausted.revision=INT64_MAX;const auto end=write_snapshot(exhausted,policy);
  refused([&]{enroll(end,"other","Label",{sample('9')});},"revision wrapped on enrollment");
  refused([&]{prepare_removal(end,policy,"speaker");},"revision wrapped on removal");
  refused([&]{prepare_reset(end,policy);},"revision wrapped on reset");
  std::cout<<"native enrollment: whole batch, canonical readback, usable decision, refusal, bounds and revision contracts PASS\n";
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
