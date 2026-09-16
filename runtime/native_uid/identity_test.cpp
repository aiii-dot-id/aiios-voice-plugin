#include "identity.h"
#include <cmath>
#include <iostream>
#include <stdexcept>
using namespace aii::uid;
void check(bool b,const char* why){if(!b)throw std::runtime_error(why);}
template<class F>void refuses(F f,const char* why){try{f();}catch(const std::invalid_argument&){return;}throw std::runtime_error(why);}
int main(){try{
  Policy p{std::string(64,'a'),std::string(64,'b'),std::string(64,'c'),.56,.105,3};
  Vector x{},y{};x[0]=1;y[1]=1;
  Snapshot s{p,0,{}};check(identify(s,p,x,p.embedding_binding).reason=="no_enrollments","empty invented speaker");
  Speaker a{"a","Éponine",{}},b{"b","Second",{}};
  for(char c='1';c<='3';++c)a.samples.push_back({std::string(64,c),x});
  for(char c='4';c<='6';++c)b.samples.push_back({std::string(64,c),y});
  s.revision=1;s.speakers={a,b};
  auto d=identify(s,p,x,p.embedding_binding);check(d.outcome=="known"&&d.speaker_id=="a"&&d.label==a.label,"known refused");
  auto bad=s;bad.policy.threshold=.1;refuses([&]{identify(bad,p,x,p.embedding_binding);},"snapshot replaced policy");
  refuses([&]{identify(s,p,x,std::string(64,'d'));},"wrong model binding accepted");
  bad=s;bad.speakers[1].samples[0].audio_sha256=bad.speakers[0].samples[0].audio_sha256;
  refuses([&]{validate(bad,p);},"duplicate evidence accepted");
  bad=s;bad.speakers[0].samples[0].embedding[0]=.9;refuses([&]{validate(bad,p);},"nonunit enrollment silently normalized");
  bad=s;bad.speakers[0].label="bad\nlabel";refuses([&]{validate(bad,p);},"control in label accepted");
  bad=s;bad.speakers[0].label="\xc0\xaf";refuses([&]{validate(bad,p);},"malformed Unicode accepted");
  bad=s;bad.speakers[0].samples.resize(2);d=identify(bad,p,x,p.embedding_binding);
  check(d.outcome=="unknown"&&d.reason=="insufficient_enrollment"&&d.speaker_id.empty(),"insufficient enrollment identified");
  bad=s;for(auto& sample:bad.speakers[1].samples)sample.embedding=x;
  bad.policy.minimum_margin=0;auto zero=p;zero.minimum_margin=0;
  d=identify(bad,zero,x,p.embedding_binding);
  check(d.outcome=="ambiguous"&&d.speaker_id.empty()&&d.label.empty(),"zero-margin tie identified");
  Vector other{};other[2]=1;d=identify(s,p,other,p.embedding_binding);
  check(d.outcome=="unknown"&&d.reason=="below_acceptance_threshold","unknown identified");
  other[0]=NAN;refuses([&]{identify(s,p,other,p.embedding_binding);},"nonfinite query accepted");
  bad=s;bad.revision=0;refuses([&]{validate(bad,p);},"enrollment without revision accepted");
  bad=s;bad.speakers[0].samples.resize(2);bad.speakers[0].samples[1].embedding[0]=-1;
  refuses([&]{validate(bad,p);},"zero centroid accepted");
  std::cout<<"native UID policy, bounds, binding, unknown, insufficient and ambiguous PASS\n";
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
