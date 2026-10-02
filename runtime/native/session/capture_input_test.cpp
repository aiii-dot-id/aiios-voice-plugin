#include "capture_input.h"
#include <iostream>
#include <stdexcept>
using namespace aii::voice;
using namespace aii::voice::wire;
void check(bool ok,const char* s){if(!ok)throw std::runtime_error(s);}
template<class F> void refuses(F f){try{f();}catch(const Refused&){return;}throw std::runtime_error("invalid capture admitted");}
Json args(){return parse("{\"request_id\":\""+std::string(64,'a')+"\",\"created_ms\":1789500000000,\"consented\":true}");}
int main(){try{
  auto a=args();CaptureInput input(a.get());
  refuses([&]{input.take();});input.feed(0,std::vector<float>(16000,.25f));
  input.finish(31920);input.finish(31920);
  refuses([&]{input.finish(32000);});refuses([&]{input.end(31920);});
  refuses([&]{input.feed(16001,std::vector<float>(15920,.5f));});
  refuses([&]{input.feed(16000,std::vector<float>(15921,.5f));});
  input.feed(16000,std::vector<float>(15920,.5f));input.end(31920);
  const auto pcm=input.take();check(pcm.size()==31920&&pcm.front()==.25f&&pcm.back()==.5f,"tail changed");
  refuses([&]{input.take();});refuses([&]{input.feed(31920,{.1f});});
  CaptureInput short_input(a.get());short_input.feed(0,{.1f});refuses([&]{short_input.end(1);});
  short_input.abandon();check(short_input.received()==0,"aborted recording retained PCM");
  refuses([&]{short_input.feed(0,{.1f});});
  CaptureInput long_input(a.get());long_input.feed(0,std::vector<float>(480000));
  refuses([&]{long_input.feed(480000,{.1f});});long_input.end(480000);
  for(const char* bad:{"{}","{\"consented\":false}","{\"request_id\":\"x\",\"created_ms\":1,\"consented\":true}"})
    refuses([&]{auto j=parse(bad);CaptureInput rejected(j.get());});
  put(a,"unknown",number(1));refuses([&]{CaptureInput rejected(a.get());});
  std::cout<<"explicit capture: exact tail, cutoff, consent, bounds and single preparation PASS\n";
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
