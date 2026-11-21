#include "native_models.h"
#include "worker_environment.h"
#include <cstring>
#include <iostream>
#include <thread>
#ifdef __APPLE__
#include <libproc.h>
#include <unistd.h>
#endif
using namespace aii::voice;
namespace {
void check(bool ok,const std::string& why){if(!ok)throw std::runtime_error(why);}
double footprint_mib() {
#ifdef __APPLE__
  rusage_info_v4 info{};
  if(proc_pid_rusage(getpid(),RUSAGE_INFO_V4,reinterpret_cast<rusage_info_t*>(&info))==0)
    return double(info.ri_phys_footprint)/(1024*1024);
#endif
  return -1;
}
uint64_t digest(const std::vector<float>& pcm) {
  uint64_t h=1469598103934665603ull;const auto* b=reinterpret_cast<const unsigned char*>(pcm.data());
  for(size_t i=0;i<pcm.size()*sizeof(float);++i){h^=b[i];h*=1099511628211ull;}
  return h;
}
struct Voice {std::string voice;uint32_t seed;std::string text;};
std::vector<float> speak(Synthesizer& tts,const Voice& v,uint64_t id) {
  SpeechSettings s;s.voice=v.voice;s.seed=v.seed;
  tts.open();tts.configure(s);tts.start(id,v.text);
  std::vector<float> all;
  for(;;){auto part=tts.next();if(part.empty())break;all.insert(all.end(),part.begin(),part.end());}
  tts.reset();check(!all.empty(),"no speech");return all;
}
}
int main(int argc,char** argv) {
  try {
    check(argc==10,"asr mel vad endpoint coeff pocket config tts_backend asr_execution required");
    configure_worker_environment(std::string(argv[8])=="vulkan");
    ModelPaths paths{argv[1],argv[2],argv[3],argv[4],argv[5],argv[6],argv[7]};paths.tts_backend=argv[8];paths.asr_execution=argv[9];
    const Voice a{"alba",20261009,"Good morning. The weather in Paris is clear and mild today."},
                b{"marius",4242,"Remind me to call the garage on Thursday about the brakes."};
    NativeModels models(paths);
    const double loaded=footprint_mib();
    auto solo_a=models.open_speech();const auto alone_a=speak(*solo_a,a,1);solo_a.reset();
    auto solo_b=models.open_speech();const auto alone_b=speak(*solo_b,b,1);solo_b.reset();
    auto pair_a=models.open_speech(),pair_b=models.open_speech();
    const double opened=footprint_mib();
    std::vector<float> together_a,together_b;std::string fault_a,fault_b;
    std::thread first([&]{try{together_a=speak(*pair_a,a,1);}catch(const std::exception& e){fault_a=e.what();}});
    std::thread second([&]{try{together_b=speak(*pair_b,b,1);}catch(const std::exception& e){fault_b=e.what();}});
    first.join();second.join();
    check(fault_a.empty(),"session A beside B: "+fault_a);check(fault_b.empty(),"session B beside A: "+fault_b);
    const bool same_a=together_a==alone_a,same_b=together_b==alone_b;
    std::cout<<"{\"footprint_mib_with_models\":"<<loaded<<",\"footprint_mib_with_two_more_sessions_speech\":"<<opened
      <<",\"alone_a\":{\"samples\":"<<alone_a.size()<<",\"digest\":"<<digest(alone_a)<<"}"
      <<",\"alone_b\":{\"samples\":"<<alone_b.size()<<",\"digest\":"<<digest(alone_b)<<"}"
      <<",\"together_a\":{\"samples\":"<<together_a.size()<<",\"digest\":"<<digest(together_a)<<"}"
      <<",\"together_b\":{\"samples\":"<<together_b.size()<<",\"digest\":"<<digest(together_b)<<"}"
      <<",\"a_exactly_as_alone\":"<<(same_a?"true":"false")<<",\"b_exactly_as_alone\":"<<(same_b?"true":"false")<<"}\n";
    return same_a&&same_b?0:2;
  }catch(const std::exception& e){std::cerr<<"speech pair probe failed: "<<e.what()<<'\n';return 1;}
}
