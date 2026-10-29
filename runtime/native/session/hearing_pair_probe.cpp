#include "native_models.h"
#include "worker_environment.h"
#include <chrono>
#include <cstring>
#include <fstream>
#include <iostream>
#include <thread>
using namespace aii::voice;
using Clock=std::chrono::steady_clock;
using namespace std::chrono_literals;
namespace {
void check(bool ok,const std::string& why){if(!ok)throw std::runtime_error(why);}
std::string quoted(const std::string& s) {
  std::string q="\"";
  for(unsigned char c:s) {
    if(c=='"'||c=='\\'){q+='\\';q+=c;}
    else if(c<32){const char* h="0123456789abcdef";q+="\\u00";q+=h[c>>4];q+=h[c&15];}
    else q+=c;
  }
  return q+'"';
}
std::vector<float> pcm(const char* path) {
  std::ifstream f(path,std::ios::binary|std::ios::ate);const auto n=f.tellg();
  check(bool(f)&&n>0&&n%4==0&&uint64_t(n)<=16000ull*60*4,"bounded float PCM required");
  std::vector<float> v(static_cast<size_t>(n)/4);f.seekg(0);f.read(reinterpret_cast<char*>(v.data()),n);
  check(bool(f),"PCM read");return v;
}
struct Heard {std::vector<std::string> finals;double seconds=0;};
Heard hear(NativeModels::SessionHearing& h,Synthesizer& tts,const std::vector<float>& input) {
  const auto begin=Clock::now();Heard heard;
  Session session(*h.recognizer,*h.vad,*h.endpoint,tts,Settings{768,.5f});
  auto pump=[&]{
    const auto state=session.status();check(state.error.empty(),state.error);
    check(std::chrono::duration<double>(Clock::now()-begin).count()<180,"hearing deadline");
    Event e;while(session.event(e))if(e.kind=="transcript_final")heard.finals.push_back(e.text);
  };
  for(size_t offset=0;offset<input.size();) {
    const auto n=std::min(size_t(512),input.size()-offset);
    if(session.feed(offset,input.data()+offset,n))offset+=n;
    pump();std::this_thread::sleep_for(1ms);
  }
  session.finish_input(input.size());
  while(!session.status().input_finished){pump();std::this_thread::sleep_for(1ms);}
  pump();session.close(false);check(session.wait_closed(30000),"session did not retire");pump();
  heard.seconds=std::chrono::duration<double>(Clock::now()-begin).count();
  return heard;
}
std::string json(const Heard& h) {
  std::string out="{\"seconds\":"+std::to_string(h.seconds)+",\"finals\":[";
  for(size_t i=0;i<h.finals.size();++i)out+=(i?",":"")+quoted(h.finals[i]);
  return out+"]}";
}
}
int main(int argc,char** argv) {
  try {
    check(argc==11,"asr mel vad endpoint coeff pocket config asr_execution pcm_a pcm_b required");
    configure_worker_environment(false);
    ModelPaths paths{argv[1],argv[2],argv[3],argv[4],argv[5],argv[6],argv[7]};paths.asr_execution=argv[8];
    const auto a=pcm(argv[9]),b=pcm(argv[10]);
    NativeModels models(paths);
    auto& tts=models.synthesizer();
    auto solo_a=models.open_hearing();const auto alone_a=hear(solo_a,tts,a);
    auto solo_b=models.open_hearing();const auto alone_b=hear(solo_b,tts,b);
    auto pair_a=models.open_hearing(),pair_b=models.open_hearing();
    Heard together_a,together_b;std::string fault_a,fault_b;
    std::thread first([&]{try{together_a=hear(pair_a,tts,a);}catch(const std::exception& e){fault_a=e.what();}});
    std::thread second([&]{try{together_b=hear(pair_b,tts,b);}catch(const std::exception& e){fault_b=e.what();}});
    first.join();second.join();
    check(fault_a.empty(),"session A beside B: "+fault_a);check(fault_b.empty(),"session B beside A: "+fault_b);
    const bool same_a=together_a.finals==alone_a.finals,same_b=together_b.finals==alone_b.finals;
    std::cout<<"{\"alone_a\":"<<json(alone_a)<<",\"alone_b\":"<<json(alone_b)
      <<",\"together_a\":"<<json(together_a)<<",\"together_b\":"<<json(together_b)
      <<",\"a_exactly_as_alone\":"<<(same_a?"true":"false")<<",\"b_exactly_as_alone\":"<<(same_b?"true":"false")<<"}\n";
    return same_a&&same_b?0:2;
  }catch(const std::exception& e){std::cerr<<"hearing pair probe failed: "<<e.what()<<'\n';return 1;}
}
