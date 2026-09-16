#include "session_evidence.h"
#include <iostream>
#include <future>
using namespace aii::uid;
void check(bool ok,const char* text){if(!ok)throw std::runtime_error(text);}
template<class F>void refuses(F f,const char* text){bool caught=false;try{f();}catch(const std::invalid_argument&){caught=true;}check(caught,text);}
int main(){try{
  SessionEvidence e;Sample s{std::string(64,'a'),{}};s.embedding[0]=1;
  const auto now=SessionEvidence::Clock::now();
  refuses([&]{e.epoch();},"unopened cache admitted evidence");
  const auto first=e.begin();e.retain(first,1,s,now);e.retain(first,2,s,now);
  check(e.available(now)==std::vector<uint64_t>({1,2}),"available evidence list differs");
  check(e.select(first,{2,1},now).size()==2,"selected whole batch missing");
  refuses([&]{e.select(first,{1,1},now);},"duplicate final accepted");
  refuses([&]{e.select(first,{1,3},now);},"partial batch accepted");
  refuses([&]{e.retain(first,1,s,now);},"final meaning replaced");
  refuses([&]{e.select(first,{},now);},"empty selection accepted");
  refuses([&]{e.select(first,{1,2,3,4,5,6,7,8,9},now);},"selection bound ignored");
  check(e.select(first,{1},now+SessionEvidence::lifetime-std::chrono::nanoseconds(1)).size()==1,"evidence expired early");
  refuses([&]{e.select(first,{1},now+SessionEvidence::lifetime);},"expired evidence retained");
  check(e.available(now+SessionEvidence::lifetime).empty(),"expired finals advertised as available");
  const auto second=e.begin();
  for(uint64_t n=1;n<=17;++n)e.retain(second,n,s,now);
  refuses([&]{e.select(second,{1},now);},"capacity did not evict oldest final");
  check(e.select(second,{2,17},now).size()==2,"live evidence evicted incorrectly");
  // Deterministically hold an inference result across abort and a new session.
  std::promise<void> release;auto go=release.get_future();
  auto late=std::async(std::launch::async,[&]{go.wait();refuses([&]{e.retain(second,18,s,now);},"old inference contaminated new session");});
  e.cancel();refuses([&]{e.select(second,{2},now);},"abort left enrollment evidence usable");
  const auto third=e.begin();release.set_value();late.get();
  refuses([&]{e.select(third,{2},now);},"new session inherited evidence");
  refuses([&]{e.select(second,{2},now);},"old epoch accepted");
  e.retain(third,1,s,now);check(e.select(third,{1},now)[0].audio_sha256==s.audio_sha256,"new session cannot collect");
  refuses([&]{e.select(second,{1},now);},"old session selected equal-numbered new-session evidence");
  std::cout<<"session evidence: exact selection, expiry, eviction, abort and late-producer fences PASS\n";
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
