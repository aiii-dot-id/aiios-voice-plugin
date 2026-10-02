#include "profile_admission.h"
#include <cmath>
#include <cstdio>
#include <iostream>
#include <stdexcept>
using namespace aii::uid;
void check(bool ok,const char* why){if(!ok)throw std::runtime_error(why);}
Sample sample(unsigned n,unsigned axis) {
  char digest[65];std::snprintf(digest,sizeof digest,"%064x",n);
  Vector v(256);v.at(axis)=1;return {digest,v};
}
int main(){try{
  const auto p=read_policy("{\"calibration_sha256\":\""+std::string(64,'a')+"\",\"embedding_binding\":\""+std::string(64,'b')+
    "\",\"minimum_enrollment_samples\":1,\"minimum_margin\":0.105,\"threshold\":0.56}");
  ProfileAdmissions a;const auto now=ProfileAdmissions::Clock::now();
  check(!a.corroborates("base",p,1,1,sample(1,0),now),"first observation admitted");
  check(!a.corroborates("base",p,1,1,sample(2,0),now),"second track of same utterance admitted");
  check(!a.corroborates("base",p,2,1,sample(1,0),now),"exact replay admitted across sessions");
  check(!a.corroborates("base",p,2,1,sample(2,0),now),"same-utterance evidence replay became corroboration");
  Sample support;
  check(a.corroborates("base",p,2,1,sample(3,0),now,&support)&&
    support.audio_sha256==sample(1,0).audio_sha256,"distinct utterance lost its corroborating recording");
  check(!a.corroborates("changed",p,3,1,sample(4,0),now),"stale registry proposal survived mutation");
  check(!a.corroborates("changed",p,4,1,sample(5,0),now+ProfileAdmissions::lifetime),"expired evidence admitted");
  a.clear();
  check(!a.corroborates("base",p,0,1,sample(1,0),now)&&a.size()==0,"missing session origin staged");
  check(!a.corroborates("base",p,1,0,sample(1,0),now)&&a.size()==0,"missing utterance origin staged");
  for(unsigned i=0;i<100;++i) {
    check(!a.corroborates("base",p,1,i+1,sample(i+1,i),now),"different people corroborated");
    check(a.size()<=ProfileAdmissions::capacity,"transient state unbounded");
  }
  a.clear();a.corroborates("base",p,1,1,sample(1,0),now);a.corroborates("base",p,1,2,sample(2,1),now);
  auto mixed=sample(3,0);mixed.embedding[0]=mixed.embedding[1]=std::sqrt(.5);
  check(!a.corroborates("base",p,1,3,mixed,now)&&a.size()==3,"ambiguous observation admitted or was discarded");
  check(!a.corroborates("base",p,2,1,mixed,now),"ambiguous evidence replay admitted");
  check(a.corroborates("base",p,1,4,sample(4,0),now),"ambiguous query erased a distinct candidate");
  // Noisy first observations of one person must not permanently compete
  // against that person's subsequent clean observations. Each clean query
  // is still only a candidate until a DIFFERENT utterance corroborates it.
  for(double c:{.70,.65}) {
    a.clear();auto noisy1=sample(1,0),noisy2=sample(2,0);
    noisy1.embedding[0]=noisy2.embedding[0]=c;
    noisy1.embedding[1]=noisy2.embedding[2]=std::sqrt(1-c*c);
    check(!a.corroborates("base",p,1,1,noisy1,now),"first noisy sample admitted");
    check(!a.corroborates("base",p,1,2,noisy2,now),"uncorroborated noisy sample admitted");
    check(!a.corroborates("base",p,1,3,sample(3,0),now)&&a.size()==3,"ambiguous clean query not retained separately");
    check(!a.corroborates("base",p,1,3,sample(4,0),now),"sibling of cleaner candidate admitted");
    check(!a.corroborates("base",p,1,4,sample(4,0),now),"sibling evidence replay admitted");
    check(a.corroborates("base",p,1,4,sample(5,0),now),"clean corroboration stranded by noisy first samples");
  }
  a.clear();
  check(!a.corroborates("base",p,1,1,sample(1,0),now),"replay-cap first observation admitted");
  for(unsigned i=2;i<=ProfileAdmissions::replay_capacity;++i)
    check(a.corroborates("base",p,1,i,sample(i,0),now),"available replay capacity refused distinct support");
  check(!a.corroborates("base",p,2,1,sample(300,0),now),"replay table evicted an unexpired fence");
  check(!a.corroborates("base",p,2,2,sample(1,0),now),"full table admitted a replay");
  a.expire(now+ProfileAdmissions::lifetime);check(a.size()==0,"expiry retained eligible proposals");
  check(!a.corroborates("base",p,3,1,sample(301,0),now+ProfileAdmissions::lifetime),"expiry manufactured corroboration");
  const auto empty=write_registry({0,{p.policy,0,{}},{}},p);
  auto known=observe_speaker(empty,p,0,"00000000-0000-4000-8000-000000000001",sample(1,0),
    ProfileAdmission::Corroborated,sample(3,0));
  auto near=sample(2,0);near.embedding[0]=.525;near.embedding[1]=std::sqrt(1-near.embedding[0]*near.embedding[0]);
  auto result=observe_speaker(known.document,p,1,"00000000-0000-4000-8000-000000000002",near);
  check(result.uuid.empty()&&result.document==known.document&&result.match->reason=="below_acceptance_threshold"&&
    result.reason=="speaker_profile_pending","near-floor single sample minted or lost diagnostics");
  std::cout<<"profile admission replay, origin, expiry, ambiguity, bound and near-floor contracts passed\n";
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
