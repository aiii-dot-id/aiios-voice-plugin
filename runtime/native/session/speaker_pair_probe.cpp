#include "native_speaker.h"
#include "uid_backend.h"
#include "worker_environment.h"
#include "../../native_uid/bound_policies.h"
#include <cstring>
#include <fstream>
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
std::string bytes(const char* path,size_t bound) {
  std::ifstream f(path,std::ios::binary|std::ios::ate);const auto n=f.tellg();
  check(bool(f)&&n>0&&uint64_t(n)<=bound,std::string("bounded input required: ")+path);
  std::string v(static_cast<size_t>(n),'\0');f.seekg(0);f.read(v.data(),n);check(bool(f),"read");return v;
}
std::vector<float> pcm(const char* path) {
  const auto raw=bytes(path,16000ull*60*4);check(raw.size()%4==0,"float PCM required");
  std::vector<float> v(raw.size()/4);std::memcpy(v.data(),raw.data(),raw.size());return v;
}
}
int main(int argc,char** argv) {
  try {
    check(argc==6,"uid_model policy_json pcm_a pcm_b shared|separate required");
    const std::string mode=argv[5];check(mode=="shared"||mode=="separate","shared or separate");
    configure_worker_environment(false);
    const aii::uid::BoundPolicies policies(bytes(argv[2],4096),std::string{});
    const auto policy=policies.current().policy;
    const auto a=pcm(argv[3]),b=pcm(argv[4]);
    const NativeSpeaker::ReadSnapshot no_storage=[]()->aii::uid::Snapshot{throw std::logic_error("this probe reads no storage");};
    auto speaker=[&]{return std::make_unique<NativeSpeaker>(argv[1],std::string(uid_backend),policy,no_storage);};
    const double before=footprint_mib();
    auto solo=speaker();const double one=footprint_mib();
    const auto alone_a=solo->prepare_capture(a),alone_b=solo->prepare_capture(b);
    const double ran=footprint_mib();
    auto over=[&]{return mode=="shared"?std::make_unique<NativeSpeaker>(solo->model(),policy,no_storage):speaker();};
    auto first=over(),second=over();
    const double three=footprint_mib();
    aii::uid::Sample together_a,together_b;std::string fault_a,fault_b;
    std::thread ta([&]{try{together_a=first->prepare_capture(a);}catch(const std::exception& e){fault_a=e.what();}});
    std::thread tb([&]{try{together_b=second->prepare_capture(b);}catch(const std::exception& e){fault_b=e.what();}});
    ta.join();tb.join();
    check(fault_a.empty(),"speaker A beside B: "+fault_a);check(fault_b.empty(),"speaker B beside A: "+fault_b);
    const double all_ran=footprint_mib();
    const bool same_a=together_a.embedding==alone_a.embedding,same_b=together_b.embedding==alone_b.embedding;
    std::cout<<"{\"backend\":\""<<uid_backend<<"\",\"mode\":\""<<mode<<"\",\"footprint_mib_before\":"<<before<<",\"with_one_speaker\":"<<one
      <<",\"after_it_ran\":"<<ran<<",\"with_three_speakers\":"<<three<<",\"after_all_ran\":"<<all_ran<<",\"embedding_dimensions\":"<<alone_a.embedding.size()
      <<",\"a_exactly_as_alone\":"<<(same_a?"true":"false")<<",\"b_exactly_as_alone\":"<<(same_b?"true":"false")
      <<",\"a_differs_from_b\":"<<(alone_a.embedding!=alone_b.embedding?"true":"false")<<"}\n";
    return same_a&&same_b?0:2;
  }catch(const std::exception& e){std::cerr<<"speaker pair probe failed: "<<e.what()<<'\n';return 1;}
}
