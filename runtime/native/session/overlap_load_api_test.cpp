// Link the actual native_models/native_c_api candidate, with model constructors
// held at deterministic seams. No inference or audio is simulated as qualified.
#include "c_api_internal.h"
#include "../../native_asr/asr.h"
#include "../../native_vad/vad.h"
#include "../../native_endpoint/endpoint.h"
#include <atomic>
#include <chrono>
#include <cstdio>
#include <fstream>
#include <future>
#include <iostream>
#include <stdexcept>
#include <string>
using namespace std::chrono_literals;
std::atomic<int> live{0};
std::promise<void> asr_release,tts_entered;
auto asr_gate=asr_release.get_future().share();
bool fail_asr=false,fail_tts=false;
struct Count {Count(){++live;}~Count(){--live;}};
struct AiiAsr:Count {}; struct AiiAsrStream {}; struct AiiVad:Count {}; struct AiiEndpoint:Count {};
struct aii_voice_models {std::unique_ptr<aii::voice::ModelOwner> owner;};
namespace aii::voice {aii_voice_models* wrap_models(std::unique_ptr<ModelOwner> o){return new aii_voice_models{std::move(o)};}}
extern "C" {
#if defined(__linux__) && !defined(__ANDROID__)
int nv_execution_info(void*,char* out,size_t n) noexcept {if(n<3)return -1;std::snprintf(out,n,"{}");return 0;}
#endif
AiiAsr* aii_asr_create(const char*,const float*,size_t,int,char* e,size_t n){asr_gate.wait();if(fail_asr){std::snprintf(e,n,"ASR construction failed");return nullptr;}return new AiiAsr;}
void aii_asr_destroy(AiiAsr* p){delete p;}
AiiAsr* aii_asr_create_configured(const char* p,const float* f,size_t n,int t,const char*,char* e,size_t c){return aii_asr_create(p,f,n,t,e,c);}
int aii_asr_execution_info(AiiAsr*,char*,size_t,size_t*,char*,size_t){return -1;}
AiiAsrStream* aii_asr_stream_create(AiiAsr*,char*,size_t){return new AiiAsrStream;}
int aii_asr_stream_destroy(AiiAsrStream* p,char*,size_t){delete p;return 0;}
int aii_asr_accept(AiiAsrStream*,const float*,size_t,char*,size_t){return 0;}
int aii_asr_finish(AiiAsrStream*,char*,size_t){return 0;}
int aii_asr_step(AiiAsrStream*,char*,size_t){return 0;}
int aii_asr_result(AiiAsrStream*,char* s,size_t,char*,size_t){*s=0;return 0;}
void aii_asr_cancel(AiiAsrStream*){}
AiiVad* aii_vad_create(const void*,size_t,char*,size_t){return new AiiVad;}
void aii_vad_destroy(AiiVad* p){delete p;}
int aii_vad_reset(AiiVad*,char*,size_t){return 0;}
int aii_vad_feed(AiiVad*,const float*,size_t,float* s,char*,size_t){*s=0;return 0;}
AiiEndpoint* aii_endpoint_create(const void*,size_t,const float*,size_t,char*,size_t){return new AiiEndpoint;}
void aii_endpoint_destroy(AiiEndpoint* p){delete p;}
int aii_endpoint_cancel_through(AiiEndpoint*,uint64_t){return 0;}
int aii_endpoint_score(AiiEndpoint*,uint64_t,const float*,size_t,double* s,float*,size_t,char*,size_t){*s=0;return 0;}
void* nv_create_bound(const char*,const char*,const char*,int,char* e,size_t n) noexcept {
 tts_entered.set_value();if(fail_tts){std::snprintf(e,n,"TTS construction failed");return nullptr;}return new Count;
}
int nv_destroy(void* p) noexcept {delete static_cast<Count*>(p);return 0;}
int nv_configure_voice(void*,const char*,float,char*,size_t) noexcept {return 0;}
int nv_start(void*,uint64_t,const char*,uint32_t,int,const char*,char*,size_t) noexcept {return 0;}
int nv_next(void*,uint64_t,float*,size_t,size_t*,char*,size_t) noexcept {return 0;}
int nv_cancel(void*,uint64_t) noexcept {return 0;}
int nv_reset(void*,uint64_t,char*,size_t) noexcept {return 0;}
}
int main(int argc,char** argv) {
 try {
  if(argc!=3)throw std::runtime_error("scenario and fixture path required");
  fail_asr=std::string(argv[1])=="asr-failure";fail_tts=std::string(argv[1])=="tts-failure";
  {std::ofstream f(argv[2],std::ios::binary);float zero=0;f.write(reinterpret_cast<char*>(&zero),4);}
  aii_voice_paths paths{argv[2],argv[2],argv[2],argv[2],argv[2],argv[2],argv[2]};
  aii_voice_models* model=nullptr;aii_voice_error error{};
  auto loaded=std::async(std::launch::async,[&]{return aii_voice_models_load_with_backend(&paths,"cpu",&model,&error);});
  const bool overlaps=tts_entered.get_future().wait_for(2s)==std::future_status::ready;
  const bool premature=loaded.wait_for(20ms)==std::future_status::ready;
  asr_release.set_value();const auto result=loaded.get();
  const bool success=result==AII_VOICE_OK&&model;
  const std::string reason=error.message;
  delete model;
  if(!overlaps)throw std::runtime_error("TTS did not load while ASR was held");
  if(premature)throw std::runtime_error("model handle published before ASR construction retired");
  if(live!=0)throw std::runtime_error("partial model construction leaked ownership");
  if(fail_asr||fail_tts){if(success||reason.find(fail_asr?"ASR construction failed":"TTS construction failed")==std::string::npos)throw std::runtime_error("model failure became successful or lost its cause");}
  else if(!success)throw std::runtime_error("complete model load refused");
  std::cout<<"actual load API publication and cleanup passed: "<<argv[1]<<'\n';
 }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
