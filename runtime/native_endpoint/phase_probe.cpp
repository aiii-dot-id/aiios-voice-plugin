// Phase sampling of the unchanged public model API; not a backend promotion.
#include "endpoint.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <dlfcn.h>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <thread>
#include <vector>
using Clock=std::chrono::steady_clock;
template<class T> std::vector<T> read(const char* path,size_t maximum) {
  std::ifstream f(path,std::ios::binary|std::ios::ate);const auto size=f.tellg();
  if(!f || size<=0 || size%sizeof(T) || uint64_t(size)>maximum)throw std::runtime_error("bounded file required");
  std::vector<T> result(size_t(size)/sizeof(T));f.seekg(0);f.read(reinterpret_cast<char*>(result.data()),size);
  if(!f)throw std::runtime_error("short read");return result;
}
double ms(Clock::time_point start,Clock::time_point end){return std::chrono::duration<double,std::milli>(end-start).count();}
int main(int argc,char**argv) {
  try {
    if(argc!=4)throw std::runtime_error("model coefficients recovery required");
    auto model=read<unsigned char>(argv[1],32411198);auto coeff=read<float>(argv[2],65920);
    auto pcm=read<float>(argv[3],353280);if(pcm.size()!=88320)throw std::runtime_error("exact recovery fixture required");
    char error[1024]{};const auto begin=Clock::now();
    std::unique_ptr<AiiEndpoint,decltype(&aii_endpoint_destroy)> owner(
      aii_endpoint_create(model.data(),model.size(),coeff.data(),coeff.size(),error,sizeof error),aii_endpoint_destroy);
    if(!owner)throw std::runtime_error(error);
    Dl_info library{};
    if(!dladdr(dlsym(RTLD_DEFAULT,"aii_endpoint_score"),&library) || !library.dli_fname)
      throw std::runtime_error("loaded endpoint library identity unavailable");
    std::cout<<std::setprecision(17)<<"{\"load_ms\":"<<ms(begin,Clock::now())
      <<",\"library\":\""<<library.dli_fname<<"\"}\n"<<std::flush;
    uint64_t id=0;
    for(size_t extent:{size_t(97280),size_t(120320),size_t(88320)}) {
      pcm.resize(extent,0);double prior=-1;
      for(int repeat=0;repeat<3;++repeat) {
        const auto query=++id;
        std::atomic<bool> done{false};double p=-1;int rc=-1;const auto start=Clock::now();
        auto first_front=Clock::time_point{},first_infer=Clock::time_point{},previous=start;
        double maximum_gap=0;
        std::thread worker([&]{rc=aii_endpoint_score(owner.get(),query,pcm.data(),pcm.size(),&p,nullptr,0,error,sizeof error);done.store(true);});
        while(!done.load()) {
          const auto now=Clock::now();maximum_gap=std::max(maximum_gap,ms(previous,now));previous=now;
          const int phase=aii_endpoint_phase(owner.get());
          if(phase==1 && first_front==Clock::time_point{})first_front=now;
          if(phase==2 && first_infer==Clock::time_point{})first_infer=now;
          if(ms(start,now)>5000){aii_endpoint_cancel_through(owner.get(),query);worker.join();throw std::runtime_error("bounded query expired");}
          std::this_thread::sleep_for(std::chrono::microseconds(100));
        }
        const auto finished=Clock::now();worker.join();
        if(rc || !std::isfinite(p) || (repeat && p!=prior))throw std::runtime_error(rc?error:"repeated probability changed");
        if(first_front==Clock::time_point{} || first_infer==Clock::time_point{})throw std::runtime_error("phase sampling missed a phase");
        prior=p;
        std::cout<<"{\"samples\":"<<extent<<",\"repeat\":"<<repeat<<",\"probability\":"<<p
          <<",\"elapsed_ms\":"<<ms(start,finished)<<",\"frontend_phase_ms\":"<<ms(first_front,first_infer)
          <<",\"inference_phase_ms\":"<<ms(first_infer,finished)<<",\"maximum_poll_gap_ms\":"<<maximum_gap<<"}\n"<<std::flush;
      }
    }
    return 0;
  }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
