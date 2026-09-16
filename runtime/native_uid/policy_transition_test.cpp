#include "enrollment.h"
#include "bound_policies.h"
#include "../native/vendor/picosha2/picosha2.h"
#include <iostream>
#include <stdexcept>
using namespace aii::uid;
namespace {
void check(bool ok,const char* reason){if(!ok)throw std::runtime_error(reason);}
template<class F> void refused(F fn,const char* needle) {
  try{fn();}catch(const std::exception& error){
    check(std::string(error.what()).find(needle)!=std::string::npos,"wrong refusal reason");return;
  }
  throw std::runtime_error(std::string("transition accepted: ")+needle);
}
PolicyDocument policy(unsigned minimum,char calibration='a',char binding='b',const char* threshold="0.56",const char* margin="0.105") {
  return read_policy("{\"calibration_sha256\":\""+std::string(64,calibration)+"\",\"embedding_binding\":\""+
    std::string(64,binding)+"\",\"minimum_enrollment_samples\":"+std::to_string(minimum)+
    ",\"minimum_margin\":"+margin+",\"threshold\":"+threshold+"}");
}
Sample sample(char digest,unsigned axis){Vector v{};v.at(axis)=1;return {std::string(64,digest),v};}
}
int main(){try {
  const auto old=policy(3),guided=policy(1,'c');
  const Snapshot source{old.policy,41,{{"ada","Ada",{sample('1',0),sample('2',0),sample('3',0)}},
      {"sam","Sam",{sample('4',1),sample('5',1),sample('6',1)}}}};
  const auto bytes=write_snapshot(source,old);
  const BoundPolicies policies(guided.canonical,old.canonical);
  check(policies.resolve(bytes).policy.fingerprint==old.policy.fingerprint&&policies.read(bytes).revision==41,
      "loading newer runtime silently reinterpreted old profile");
  refused([&]{BoundPolicies(guided.canonical).read(bytes);},"not bound");
  refused([&]{BoundPolicies(guided.canonical,policy(3,'a','b',"0.55").canonical);},"preserve model");
  const auto prepared=prepare_guided_policy_transition(bytes,old,guided);
  const auto converted=read_snapshot(prepared.snapshot,guided);
  check(policies.resolve(prepared.snapshot).policy.fingerprint==guided.policy.fingerprint,"new policy not selected after explicit transition");
  check(prepared.base_sha256==picosha2::hash256_hex_string(bytes)&&prepared.revision==42&&converted.revision==42,
      "transition lost exact CAS base or revision");
  // Policy/revision alone may differ. This byte equality also covers every
  // vector bit, label, ID, ordering and recording digest, not just row counts.
  auto roundtrip=converted;roundtrip.policy=source.policy;roundtrip.revision=source.revision;
  check(write_snapshot(roundtrip,old)==bytes,"transition modified enrolled identity evidence");
  for(unsigned axis=0;axis<3;++axis){
    Vector query{};query[axis]=1;
    const auto before=identify(source,old.policy,query,old.policy.embedding_binding);
    const auto after=identify(converted,guided.policy,query,guided.policy.embedding_binding);
    check(before.outcome==after.outcome&&before.speaker_id==after.speaker_id&&before.score==after.score&&before.margin==after.margin,
        "transition changed existing known/unknown decisions");
  }
  auto incomplete=source;incomplete.speakers.at(1).samples.pop_back();
  const auto insufficient=write_snapshot(incomplete,old);
  check(policies.read(insufficient).speakers.at(1).samples.size()==2&&
      policies.resolve(insufficient).policy.minimum_enrollment_samples==3,"incomplete old profile silently activated on read");
  refused([&]{policies.read(bytes+" ");},"not canonical");
  refused([&]{policies.read(write_snapshot({policy(3,'d').policy,0,{}},policy(3,'d')));},"not bound");
  refused([&]{prepare_guided_policy_transition(insufficient,old,guided);},"incomplete existing enrollment requires operator review: sam");
  check(write_snapshot(incomplete,old)==insufficient,"refusal changed existing profile");
  const auto empty=write_snapshot({old.policy,0,{}},old);
  check(read_snapshot(prepare_guided_policy_transition(empty,old,guided).snapshot,guided).speakers.empty(),
      "empty transition invented speakers");
  refused([&]{prepare_guided_policy_transition("",old,guided);},"snapshot");
  refused([&]{prepare_guided_policy_transition(bytes,old,policy(1));},"separately bound calibration");
  refused([&]{prepare_guided_policy_transition(bytes,old,policy(1,'c','d'));},"preserve model");
  refused([&]{prepare_guided_policy_transition(bytes,old,policy(1,'c','b',"0.55"));},"preserve model");
  refused([&]{prepare_guided_policy_transition(bytes,old,policy(1,'c','b',"0.56","0.10"));},"preserve model");
  refused([&]{prepare_guided_policy_transition(bytes,old,policy(2,'c'));},"only the three-recording");
  refused([&]{prepare_guided_policy_transition(prepared.snapshot,guided,old);},"only the three-recording");
  auto exhausted=source;exhausted.revision=INT64_MAX;
  refused([&]{prepare_guided_policy_transition(write_snapshot(exhausted,old),old,guided);},"revision exhausted");
  // Mutating only the in-memory struct must not trick canonical admission.
  auto mismatched=guided;mismatched.policy.threshold=.1;
  check(prepare_guided_policy_transition(bytes,old,mismatched).snapshot==prepared.snapshot,
      "noncanonical in-memory override altered policy");
  std::cout<<"guided enrollment transition: exact identity preservation, unchanged decisions, incomplete-profile refusal, CAS and bounds PASS\n";
}catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}}
