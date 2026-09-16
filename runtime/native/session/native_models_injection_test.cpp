#include "native_models.h"
#include "c_api_internal.h"
#include "../../native_asr/asr.h"
#include "../../native_vad/vad.h"
#include "../../native_endpoint/endpoint.h"
#include <cstring>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>

namespace {
int defaults=0, destroyed=0, live_vad=0, live_endpoint=0, live_tts=0;
bool refuse_vad=false;
void need(bool value,const char* why){if(!value)throw std::runtime_error(why);}
struct Replacement:aii::voice::Recognizer {
  ~Replacement()override{++destroyed;}
  void begin()override{}
  std::string push(const float*,size_t)override{return "replacement";}
  std::string finish()override{return "replacement";}
  void reset()override{}
  void cancel()noexcept override{}
};
}
extern "C" {
#if defined(__linux__) && !defined(__ANDROID__)
int nv_execution_info(void*,char* out,size_t n) noexcept {if(n<3)return -1;std::strcpy(out,"{}");return 0;}
#endif
AiiAsr* aii_asr_create(const char*,const float*,size_t,int,char* error,size_t){
  ++defaults;std::strcpy(error,"default ASR must be explicit");return nullptr;
}
void aii_asr_destroy(AiiAsr*){}
AiiAsr* aii_asr_create_configured(const char* p,const float* f,size_t n,int t,const char*,char* e,size_t c){return aii_asr_create(p,f,n,t,e,c);}
int aii_asr_execution_info(AiiAsr*,char*,size_t,size_t*,char*,size_t){return -1;}
AiiAsrStream* aii_asr_stream_create(AiiAsr*,char*,size_t){return nullptr;}
int aii_asr_stream_destroy(AiiAsrStream*,char*,size_t){return 0;}
int aii_asr_accept(AiiAsrStream*,const float*,size_t,char*,size_t){return 0;}
int aii_asr_finish(AiiAsrStream*,char*,size_t){return 0;}
int aii_asr_step(AiiAsrStream*,char*,size_t){return 0;}
int aii_asr_result(AiiAsrStream*,char*,size_t,char*,size_t){return 0;}
void aii_asr_cancel(AiiAsrStream*){}
AiiVad* aii_vad_create(const void*,size_t,char* error,size_t){
  if(refuse_vad){std::strcpy(error,"VAD admission failed");return nullptr;}
  ++live_vad;return reinterpret_cast<AiiVad*>(new int(1));
}
int aii_vad_feed(AiiVad*,const float*,size_t,float* out,char*,size_t){*out=.1f;return 0;}
int aii_vad_reset(AiiVad*,char*,size_t){return 0;}
void aii_vad_destroy(AiiVad* p){--live_vad;delete reinterpret_cast<int*>(p);}
AiiEndpoint* aii_endpoint_create(const void*,size_t,const float*,size_t,char*,size_t){
  ++live_endpoint;return reinterpret_cast<AiiEndpoint*>(new int(1));
}
int aii_endpoint_score(AiiEndpoint*,uint64_t,const float*,size_t,double* out,float*,size_t,char*,size_t){*out=.5;return 0;}
int aii_endpoint_cancel_through(AiiEndpoint*,uint64_t){return 0;}
void aii_endpoint_destroy(AiiEndpoint* p){--live_endpoint;delete reinterpret_cast<int*>(p);}
void* nv_create_bound(const char*,const char*,const char*,int,char*,size_t)noexcept{++live_tts;return new int(1);}
int nv_configure_voice(void*,const char*,float,char*,size_t)noexcept{return 0;}
int nv_start(void*,uint64_t,const char*,uint32_t,int,const char*,char*,size_t)noexcept{return 0;}
int nv_next(void*,uint64_t,float* pcm,size_t,size_t* count,char*,size_t)noexcept{pcm[0]=.1f;*count=1;return 0;}
int nv_cancel(void*,uint64_t)noexcept{return 0;}
int nv_reset(void*,uint64_t,char*,size_t)noexcept{return 0;}
int nv_destroy(void* p)noexcept{--live_tts;delete static_cast<int*>(p);return 0;}
}
int main(int argc,char** argv){
  try {
    need(argc==2,"existing fixture parent required");
    const auto root=std::filesystem::path(argv[1])/("model-injection-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    need(std::filesystem::create_directory(root),"fresh directory required");
    struct Cleanup {std::filesystem::path path;~Cleanup(){std::error_code e;std::filesystem::remove_all(path,e);}} cleanup{root};
    const auto f=(root/"coefficients.f32").string();
    {std::ofstream out(f,std::ios::binary);float value=1;out.write(reinterpret_cast<char*>(&value),4);need(bool(out),"fixture write");}
    const aii::voice::ModelPaths paths{"not-a-model",f,f,f,f,"not-a-tts-model","not-a-config","cpu"};
    auto r=std::make_unique<Replacement>();auto* expected=r.get();
    {aii::voice::NativeModels models(paths,std::move(r));
      need(&models.recognizer()==expected && defaults==0,"injected ASR still constructs default weights");
      need(live_vad==1 && live_endpoint==1 && live_tts==1,"other components not composed");}
    need(destroyed==1 && !live_vad && !live_endpoint && !live_tts,"component ownership leak");
    refuse_vad=true;
    try {aii::voice::NativeModels models(paths,std::make_unique<Replacement>());throw std::runtime_error("VAD failure hidden");}
    catch(const std::exception& e){need(std::string(e.what())=="VAD admission failed","wrong construction failure");}
    need(destroyed==2 && defaults==0,"replacement lost after downstream load failure");refuse_vad=false;
    try {aii::voice::NativeModels models(paths);throw std::runtime_error("default constructor changed");}
    catch(const std::exception& e){need(std::string(e.what())=="default ASR must be explicit","wrong default failure");}
    need(defaults==1,"old default no longer selected");
    aii_voice_paths cp{nullptr,f.c_str(),f.c_str(),f.c_str(),f.c_str(),"tts","config"};
    aii_voice_models* owner=nullptr;aii_voice_error error{};
    need(aii_voice_models_load_with_backend(&cp,"cpu",&owner,&error)==AII_VOICE_INVALID && !owner,"public load accepted missing ASR");
    need(aii::voice::load_native_models(&cp,"cpu",nullptr,nullptr,0,nullptr,nullptr,&owner,&error,
      std::make_unique<Replacement>())==AII_VOICE_OK && owner,"private injected load failed");
    aii_voice_readiness ready{};
    need(aii_voice_models_warm(owner,&ready,&error)==AII_VOICE_OK,"warm failed");
    need(std::string(ready.accelerator)=="external_recognizer","replacement was falsely labeled CPU");
    need(aii_voice_models_release(&owner,&error)==AII_VOICE_OK && !owner,"owner release failed");
    need(destroyed==3 && defaults==1 && !live_vad && !live_endpoint && !live_tts,"factory changed default or leaked component");
    std::cout<<"Native recognizer injection, default preservation, downstream-failure custody PASS\n";return 0;
  }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
