// Diagnostic only: same model/input, separating execution-thread lifetime
// from observer polling. Never changes affinity, priority, clocks or FPCR.
#include "endpoint.h"
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <dlfcn.h>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <mutex>
#include <sched.h>
#include <stdexcept>
#include <string>
#include <sys/resource.h>
#include <thread>
#include <vector>
using Clock=std::chrono::steady_clock;
template<class T> std::vector<T> read(const char* name,size_t cap) {
  std::ifstream f(name,std::ios::binary|std::ios::ate);const auto size=f.tellg();
  if(!f||size<=0||size%sizeof(T)||uint64_t(size)>cap)throw std::runtime_error("bounded fixture required");
  std::vector<T> value(size_t(size)/sizeof(T));f.seekg(0);f.read(reinterpret_cast<char*>(value.data()),size);
  if(!f)throw std::runtime_error("short fixture");return value;
}
double millis(Clock::time_point a,Clock::time_point b) {return std::chrono::duration<double,std::milli>(b-a).count();}
double cpu_ms() {timespec t{};if(clock_gettime(CLOCK_THREAD_CPUTIME_ID,&t))throw std::runtime_error("thread clock unavailable");return t.tv_sec*1000.0+t.tv_nsec/1e6;}
long frequency(int cpu) {long value=-1;std::ifstream f("/sys/devices/system/cpu/cpu"+std::to_string(cpu)+"/cpufreq/scaling_cur_freq");if(f)f>>value;return value;}
uint64_t fpcr() {
#ifdef __aarch64__
  uint64_t value;asm volatile("mrs %0, fpcr":"=r"(value));return value;
#else
  return 0;
#endif
}
struct Result {int rc=-1,cpu_begin=-1,cpu_end=-1,nice=0;long hz_begin=-1,hz_end=-1,voluntary=0,involuntary=0;double cpu=0,wall=0,p=-1;uint64_t fp=0;char error[1024]{};};
int main(int argc,char** argv) {
  try {
    if(argc!=4)throw std::runtime_error("model coefficients recovery required");
    auto model=read<unsigned char>(argv[1],32411198);auto coeff=read<float>(argv[2],65920);
    auto original=read<float>(argv[3],353280);if(original.size()!=88320)throw std::runtime_error("recovery extent differs");
    Dl_info library{};if(!dladdr(dlsym(RTLD_DEFAULT,"aii_endpoint_score"),&library))throw std::runtime_error("library unknown");
    cpu_set_t affinity;CPU_ZERO(&affinity);if(sched_getaffinity(0,sizeof affinity,&affinity))throw std::runtime_error("affinity unreadable");
    std::cout<<std::setprecision(17)<<"{\"library\":\""<<library.dli_fname<<"\",\"affinity\":[";
    bool comma=false;for(int cpu=0;cpu<CPU_SETSIZE;++cpu)if(CPU_ISSET(cpu,&affinity)){if(comma)std::cout<<',';std::cout<<cpu;comma=true;}
    std::cout<<"]}\n"<<std::flush;
    for(const std::string mode:{"direct","fresh_join","fresh_poll","persistent"}) {
      char error[1024]{};
      std::unique_ptr<AiiEndpoint,decltype(&aii_endpoint_destroy)> owner(aii_endpoint_create(model.data(),model.size(),coeff.data(),coeff.size(),error,sizeof error),aii_endpoint_destroy);
      if(!owner)throw std::runtime_error(error);
      uint64_t id=0;std::vector<float> pcm;Result result;std::atomic<bool> done{false};
      auto score=[&] {
        try {
          result.cpu_begin=sched_getcpu();result.hz_begin=frequency(result.cpu_begin);
          result.nice=getpriority(PRIO_PROCESS,0);result.fp=fpcr();rusage before{},after{};
          if(getrusage(RUSAGE_THREAD,&before))throw std::runtime_error("usage unavailable");
          const auto cpu=cpu_ms();const auto start=Clock::now();
          result.rc=aii_endpoint_score(owner.get(),id,pcm.data(),pcm.size(),&result.p,nullptr,0,result.error,sizeof result.error);
          result.wall=millis(start,Clock::now());result.cpu=cpu_ms()-cpu;
          if(getrusage(RUSAGE_THREAD,&after))throw std::runtime_error("usage unavailable");
          result.voluntary=after.ru_nvcsw-before.ru_nvcsw;result.involuntary=after.ru_nivcsw-before.ru_nivcsw;
          result.cpu_end=sched_getcpu();result.hz_end=frequency(result.cpu_end);
        } catch(...) {result.rc=-2;}
        done.store(true);
      };
      std::mutex mutex;std::condition_variable changed;bool pending=false,stop=false;
      std::thread persistent;
      if(mode=="persistent")persistent=std::thread([&]{
        std::unique_lock<std::mutex> lock(mutex);
        for(;;){changed.wait(lock,[&]{return stop||pending;});if(stop)return;pending=false;lock.unlock();score();changed.notify_all();lock.lock();}
      });
      bool failed=false;
      for(size_t extent:{size_t(97280),size_t(120320),size_t(88320)}) {
        pcm=original;pcm.resize(extent,0);
        for(int repeat=0;repeat<3;++repeat) {
          ++id;result=Result{};done.store(false);const auto started=Clock::now();
          if(mode=="direct")score();
          else if(mode=="persistent") {
            std::unique_lock<std::mutex> lock(mutex);pending=true;changed.notify_all();
            changed.wait(lock,[&]{return done.load();});
          } else {
            std::thread worker(score);
            if(mode=="fresh_poll")while(!done.load()){
              (void)aii_endpoint_phase(owner.get());std::this_thread::sleep_for(std::chrono::microseconds(100));
            }
            worker.join();
          }
          const auto elapsed=millis(started,Clock::now());
          if(result.rc||!std::isfinite(result.p)){failed=true;break;}
          std::cout<<"{\"mode\":\""<<mode<<"\",\"samples\":"<<extent<<",\"repeat\":"<<repeat<<",\"probability\":"<<result.p
            <<",\"elapsed_ms\":"<<elapsed<<",\"call_ms\":"<<result.wall<<",\"thread_cpu_ms\":"<<result.cpu
            <<",\"cpu_begin\":"<<result.cpu_begin<<",\"cpu_end\":"<<result.cpu_end<<",\"khz_begin\":"<<result.hz_begin<<",\"khz_end\":"<<result.hz_end
            <<",\"voluntary_switches\":"<<result.voluntary<<",\"involuntary_switches\":"<<result.involuntary<<",\"nice\":"<<result.nice<<",\"fpcr\":"<<result.fp<<"}\n"<<std::flush;
        }
        if(failed)break;
      }
      if(persistent.joinable()){{std::lock_guard<std::mutex> lock(mutex);stop=true;}changed.notify_all();persistent.join();}
      if(failed)throw std::runtime_error(result.error[0]?result.error:"query failed");
    }
    return 0;
  }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
