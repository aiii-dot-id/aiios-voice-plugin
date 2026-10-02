// Diagnostic executable against the unchanged resident DLL. No audio devices.
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#define NOMINMAX
#include <windows.h>
#include <psapi.h>
#define IMPORT extern "C" __declspec(dllimport)
IMPORT void* nv_create_bound(const char*,const char*,const char*,int,char*,size_t) noexcept;
IMPORT int nv_start(void*,uint64_t,const char*,uint32_t,int,const char*,char*,size_t) noexcept;
IMPORT int nv_next(void*,uint64_t,float*,size_t,size_t*,char*,size_t) noexcept;
IMPORT int nv_cancel(void*,uint64_t) noexcept;
IMPORT uint32_t nv_state(void*) noexcept;
IMPORT int nv_reset(void*,uint64_t,char*,size_t) noexcept;
IMPORT int nv_destroy(void*) noexcept;
IMPORT int nv_profile_snapshot(uint64_t*,size_t,int);
using Clock=std::chrono::steady_clock;
double seconds(Clock::time_point t){return std::chrono::duration<double>(Clock::now()-t).count();}
void require(bool ok,const char* message){if(!ok)throw std::runtime_error(message);}
std::array<uint64_t,4> profile(bool reset){std::array<uint64_t,4> p{};require(nv_profile_snapshot(p.data(),p.size(),reset)==0,"profile refused");return p;}
struct Owner{void* model=nullptr;~Owner(){if(model && nv_destroy(model)!=0)std::terminate();}};
uint64_t ticks(FILETIME t){return (uint64_t(t.dwHighDateTime)<<32)|t.dwLowDateTime;}
void resources(){
  PROCESS_MEMORY_COUNTERS_EX m{};m.cb=static_cast<DWORD>(sizeof(m));
  FILETIME created{},ended{},kernel{},user{},idle{},system_kernel{},system_user{};
  require(GetProcessMemoryInfo(GetCurrentProcess(),reinterpret_cast<PROCESS_MEMORY_COUNTERS*>(&m),static_cast<DWORD>(sizeof(m)))!=0,"memory observation failed");
  require(GetProcessTimes(GetCurrentProcess(),&created,&ended,&kernel,&user)!=0,"process timing failed");
  require(GetSystemTimes(&idle,&system_kernel,&system_user)!=0,"system timing failed");
  std::cout<<",\"resources\":{\"working_bytes\":"<<m.WorkingSetSize<<",\"peak_working_bytes\":"<<m.PeakWorkingSetSize
    <<",\"private_bytes\":"<<m.PrivateUsage<<",\"process_cpu_100ns\":"<<(ticks(kernel)+ticks(user))
    <<",\"system_100ns\":["<<ticks(idle)<<','<<ticks(system_kernel)<<','<<ticks(system_user)<<"]}";
}
int main(int argc,char** argv){
 try {
  require(argc==5,"model, config, output prefix, threads required");
  const int threads=std::stoi(argv[4]);require(threads==1||threads==2||threads==4,"thread budget refused");
  char error[1024]{};const auto load=Clock::now();Owner owner;
  owner.model=nv_create_bound(argv[1],argv[2],"vulkan",threads,error,sizeof error);require(owner.model,error);
  std::cout<<std::setprecision(17)<<"{\"type\":\"ready\",\"threads\":"<<threads<<",\"seconds\":"<<seconds(load);resources();std::cout<<"}"<<std::endl;
  auto synth=[&](uint64_t generation,const char* name,const char* text){
    profile(true);const auto begin=Clock::now();
    require(nv_start(owner.model,generation,text,20260908,750,nullptr,error,sizeof error)==0,error);
    const double prepare=seconds(begin);const auto prepared_profile=profile(false);
    std::vector<float> all;std::array<float,1920> pcm{};double first=-1;size_t chunks=0;
    std::array<uint64_t,4> first_profile{};
    for(;;){size_t count=0;const int rc=nv_next(owner.model,generation,pcm.data(),pcm.size(),&count,error,sizeof error);
      require(rc==0||rc==1,error);if(rc==0){require(count==0,"EOS carried unexpected samples");break;}
      require(count>0&&count<=1920,"invalid audio chunk");if(first<0){first=seconds(begin);first_profile=profile(false);}
      for(size_t i=0;i<count;++i)require(std::isfinite(pcm[i]),"nonfinite PCM");
      all.insert(all.end(),pcm.begin(),pcm.begin()+count);require(all.size()<=1440000,"audio bound exceeded");++chunks;
    }
    const double total=seconds(begin);require(!all.empty(),"no natural audio");auto p=profile(false);
    std::ofstream file(std::string(argv[3])+"-"+name+".f32",std::ios::binary);require(bool(file),"output refused");
    file.write(reinterpret_cast<const char*>(all.data()),std::streamsize(all.size()*sizeof(float)));file.close();require(bool(file),"output write failed");
    require(nv_reset(owner.model,generation,error,sizeof error)==0,error);
    std::cout<<"{\"type\":\"synthesis\",\"name\":\""<<name<<"\",\"generation\":"<<generation
      <<",\"samples\":"<<all.size()<<",\"chunks\":"<<chunks<<",\"prepare_seconds\":"<<prepare
      <<",\"first_pcm_seconds\":"<<first<<",\"seconds\":"<<total
      <<",\"after_start_profile_ns\":["<<prepared_profile[0]<<','<<prepared_profile[1]<<','<<prepared_profile[2]<<','<<prepared_profile[3]<<"]"
      <<",\"first_pcm_profile_ns\":["<<first_profile[0]<<','<<first_profile[1]<<','<<first_profile[2]<<','<<first_profile[3]<<"]"
      <<",\"profile_ns\":["<<p[0]<<','<<p[1]<<','<<p[2]<<','<<p[3]<<"]";resources();std::cout<<"}"<<std::endl;
    return all;
  };
  const char* text="The local voice service is ready. Keep the words cobalt lantern seventeen.";
  const auto reference=synth(1,"first",text);
  synth(2,"second","This is a longer spoken response. You can interrupt whenever you need to. I will stop speaking, keep your opening words, and listen to what you say next.");
  require(nv_start(owner.model,3,text,20260908,750,nullptr,error,sizeof error)==0,error);
  std::array<float,1920> pcm{};size_t count=0;
  require(nv_next(owner.model,3,pcm.data(),pcm.size(),&count,error,sizeof error)==1,"interrupt first PCM missing");
  int verdict=99;size_t stale=999;char async_error[1024]{};
  std::thread inference([&]{verdict=nv_next(owner.model,3,pcm.data(),pcm.size(),&stale,async_error,sizeof async_error);});
  const auto observe=Clock::now();while(!(nv_state(owner.model)&1u)&&seconds(observe)<2)std::this_thread::yield();
  const bool computing=(nv_state(owner.model)&1u)!=0;const auto stop=Clock::now();const int cancel=nv_cancel(owner.model,3);const double admitted=seconds(stop);
  inference.join();const double retired=seconds(stop);
  require(computing&&cancel==0&&verdict==-2&&stale==0,"in-flight cancel or stale publication gate failed");
  require(nv_reset(owner.model,3,error,sizeof error)==0,error);
  std::cout<<"{\"type\":\"cancel\",\"observed_computing\":true,\"stale_samples\":0,\"admission_seconds\":"<<admitted<<",\"retirement_seconds\":"<<retired<<"}"<<std::endl;
  const auto recovery=synth(4,"recovery",text);require(reference==recovery,"same-process recovery PCM changed");
  require(nv_destroy(owner.model)==0,"model retirement refused");owner.model=nullptr;
  std::cout<<"{\"type\":\"complete\",\"natural_eos\":true,\"recovery_equal\":true}"<<std::endl;return 0;
 }catch(const std::exception& e){std::cerr<<e.what()<<std::endl;return 1;}
}
