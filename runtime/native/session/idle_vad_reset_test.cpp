#include "session.h"
#include "idle_vad_reset.h"
#include <atomic>
#include <chrono>
#include <iostream>
#include <thread>
using namespace aii::voice;
using namespace std::chrono_literals;
void need(bool value,const char* text){if(!value)throw std::runtime_error(text);}
template<class F>void until(F f){auto end=std::chrono::steady_clock::now()+3s;while(!f()){need(std::chrono::steady_clock::now()<end,"idle reset test deadline");std::this_thread::yield();}}
struct A:Recognizer {
  void begin()override{}std::string push(const float*,size_t)override{return "opening words retained";}
  std::string finish()override{return "opening words retained";}void reset()override{}void cancel()noexcept override{}
};
struct V:Vad {
  std::atomic<int> resets{0};bool fail=false;
  void reset()override{++resets;if(fail&&resets>1)throw std::runtime_error("VAD refresh failed");}
  float score(const float* p)override{return p[0]>.1f?.9f:0.f;}
};
struct E:Endpoint {double score(uint64_t,const std::vector<float>&)override{return .9;}void cancel()noexcept override{}};
struct T:Synthesizer {void start(uint64_t,const std::string&)override{}std::vector<float> next()override{return{};}void reset()override{}void cancel(uint64_t)noexcept override{}};
void run(size_t quiet,size_t speech,size_t packet,int expected){
  A a;V v;E e;T t;Session s(a,v,e,t);
  std::vector<float> pcm(quiet,0);pcm.resize(quiet+speech,.9f);
  for(size_t offset=0;offset<pcm.size();){size_t n=std::min(packet,pcm.size()-offset);until([&]{return s.feed(offset,pcm.data()+offset,n);});offset+=n;}
  s.finish_input(pcm.size());until([&]{return s.status().input_finished || !s.status().error.empty();});
  need(s.status().error.empty(),"healthy idle reset faulted");need(s.status().recognized==pcm.size(),"refresh moved the input clock");
  need(v.resets==expected,"VAD reset count ignored source silence, speech, or final padding");
  if(speech){bool final=false;Event event;while(s.event(event))if(event.kind=="transcript_final"&&event.text=="opening words retained")final=true;need(final,"post-idle opening words absent");}
  s.close(false);need(s.wait_closed(1000),"idle reset owners did not retire");
}
int main(){try{
  for(size_t packet:{size_t(241),size_t(512),size_t(4096)}){
    run(80384,512,packet,2);run(79872,512,packet,1);run(79999,0,packet,1);
    run(80000,0,packet,2);run(160768,0,packet,3);run(0,100000,packet,1);
  }
  IdleVadReset c;bool invalid=false;try{c.observe(false,0);}catch(const std::invalid_argument&){invalid=true;}need(invalid,"invalid source count accepted");
  A a;V v;v.fail=true;E e;T t;Session s(a,v,e,t);std::vector<float> quiet(80384,0);
  for(size_t off=0;off<quiet.size();){const auto n=std::min(size_t(4096),quiet.size()-off);until([&]{return s.feed(off,quiet.data()+off,n);});off+=n;}
  until([&]{return !s.status().error.empty();});need(s.status().error=="VAD refresh failed","reset failure became absence");
  need(!s.status().input_finished,"reset failure invented completion");s.close(true);need(s.wait_closed(1000),"failed refresh owners did not retire");
  std::cout<<"idle VAD: source-clock reset, padding, speech boundary, repetition, input tails and failure custody PASS\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
