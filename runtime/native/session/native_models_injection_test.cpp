#include "native_models.h"
#include "c_api_internal.h"
#include "../../native_asr/asr.h"
#include "../../native_vad/vad.h"
#include "../../native_endpoint/endpoint.h"
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <filesystem>
#include <fstream>
#include <future>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <thread>

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
// A speech model double. Its memory outlives its destruction, so a call made
// with one already destroyed is counted here where the library would read
// freed memory; each says which load it was, so a readback shows whose
// description it gives.
struct Speech {
  const int load; std::atomic<bool> alive{true};
  explicit Speech(int n):load(n){}
};
std::mutex speech_guard; std::deque<Speech> speech_models;
std::atomic<int> speech_loads{0}, refuse_speech{0};
// Holds a model call where it is, on the thread that made it, until the test
// has looked: the library takes that long to load another language's model.
struct Gate {
  std::mutex m; std::condition_variable cv; bool armed=false, reached=false, opened=false;
  void arm(){std::lock_guard<std::mutex> lock(m);armed=true;reached=opened=false;}
  void pass(){
    std::unique_lock<std::mutex> lock(m);if(!armed)return;
    reached=true;cv.notify_all();cv.wait(lock,[&]{return opened;});armed=false;
  }
  bool wait(){std::unique_lock<std::mutex> lock(m);return cv.wait_for(lock,std::chrono::seconds(5),[&]{return reached;});}
  void open(){std::lock_guard<std::mutex> lock(m);opened=true;cv.notify_all();}
};
Gate releasing, loading;
#if defined(__linux__) && !defined(__ANDROID__)
std::atomic<int> asked{0}, asked_destroyed{0}, asked_absent{0};
#endif
}
extern "C" {
#if defined(__linux__) && !defined(__ANDROID__)
// As the library answers for no model: an error. A destroyed one is freed
// memory there; here it is counted and refused.
int nv_execution_info(void* p,char* out,size_t n) noexcept {
  ++asked;
  if(!p){++asked_absent;return -1;}
  const auto* speech=static_cast<Speech*>(p);
  if(!speech->alive){++asked_destroyed;return -1;}
  const int written=std::snprintf(out,n,"{\"speech_load\":%d}",speech->load);
  return written>0&&size_t(written)<n?0:-1;
}
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
void* nv_create_bound(const char*,const char*,const char*,int,char* error,size_t capacity)noexcept{
  loading.pass();
  if(refuse_speech>0){--refuse_speech;std::snprintf(error,capacity,"speech load refused");return nullptr;}
  std::lock_guard<std::mutex> lock(speech_guard);
  ++live_tts;return &speech_models.emplace_back(++speech_loads);
}
int nv_configure_voice(void*,const char*,float,char*,size_t)noexcept{return 0;}
int nv_start(void*,uint64_t,const char*,uint32_t,int,const char*,char*,size_t)noexcept{return 0;}
int nv_next(void*,uint64_t,float* pcm,size_t,size_t* count,char*,size_t)noexcept{pcm[0]=.1f;*count=1;return 0;}
int nv_cancel(void*,uint64_t)noexcept{return 0;}
int nv_reset(void*,uint64_t,char*,size_t)noexcept{return 0;}
int nv_destroy(void* p)noexcept{
  {std::lock_guard<std::mutex> lock(speech_guard);--live_tts;}
  static_cast<Speech*>(p)->alive=false;releasing.pass();return 0;
}
}
#if defined(__linux__) && !defined(__ANDROID__)
namespace {
struct Readback {aii_voice_result result;std::string text,error;};
// The readback a status request makes, by the entry point the worker uses.
Readback readback(aii_voice_models* owner){
  char text[16385]{};size_t required=0;aii_voice_error error{};
  const auto result=aii_voice_models_execution(owner,text,sizeof text,&required,&error);
  return {result,text,error.message};
}
// Which load's speech model a readback describes; 0 when it describes none.
int described(const Readback& r){
  const std::string key="\"tts\":{\"speech_load\":";
  const auto at=r.text.find(key);
  return r.result==AII_VOICE_OK&&at!=std::string::npos?std::atoi(r.text.c_str()+at+key.size()):0;
}
std::string said(const Readback& r){return r.result==AII_VOICE_OK?r.text:"refused ("+r.error+")";}
// Asked while an open is held inside the speech model's owner. It must not
// wait for that owner: one that does is let go by letting the open go on,
// and is said.
Readback held_readback(aii_voice_models* owner){
  auto pending=std::async(std::launch::async,[owner]{return readback(owner);});
  if(pending.wait_for(std::chrono::seconds(3))==std::future_status::ready)return pending.get();
  releasing.open();loading.open();pending.wait();
  throw std::runtime_error("a status readback waited for the speech model's owner while another language was loading");
}
aii_voice_result open_session(aii_voice_models* owner,const aii_voice_speech_settings& speech,aii_voice_session** session,aii_voice_error* error){
  const aii_voice_open_options options{nullptr,&speech,0,0};
  return aii_voice_open_session(owner,&options,session,error);
}
void retire(aii_voice_session*& session){
  aii_voice_error error{};
  need(aii_voice_close(session,1,&error)==AII_VOICE_OK&&aii_voice_wait(session,5000,&error)==AII_VOICE_OK&&
       aii_voice_release(&session,&error)==AII_VOICE_OK&&!session,"speech fixture session did not retire");
}
// An open on its own thread, as the worker makes it. However the test
// leaves, the gates are opened and the thread is joined.
struct Opening {
  aii_voice_session* session=nullptr;aii_voice_error error{};aii_voice_result result=AII_VOICE_FAILED;
  std::thread thread;
  Opening(aii_voice_models* owner,const aii_voice_speech_settings& speech)
      :thread([this,owner,&speech]{result=open_session(owner,speech,&session,&error);}){}
  void join(){releasing.open();loading.open();if(thread.joinable())thread.join();}
  ~Opening(){join();}
};
// WHAT A STATUS REQUEST READS WHILE ANOTHER LANGUAGE'S MODEL IS LOADING.
// A session that changes the
// speaking language releases the resident speech model and loads another, on
// the thread that opens it. A status request on another thread used to ask
// that model where it runs: a model being released (freed memory in the
// library) or no model (a refused readback, which the worker takes as the
// engine's failure). These hold the readback to: it never asks a model and
// never waits for a load; until a change of language is done it describes
// the model before it, and then the one resident, whichever that came to
// be; with none resident it is refused in the words it always was. The
// speech model here is a double: nothing of the library itself is run.
void execution_readback_contract(const std::filesystem::path& root,const std::string& f){
  namespace fs=std::filesystem;
  const auto speech=root/"speech";
  for(const auto& at:{speech,speech/"languages"/"french"}){
    fs::create_directories(at/"embeddings");
    for(const char* name:{"model.safetensors","tokenizer.model","config.yaml","embeddings/alba.safetensors"}){
      std::ofstream out(at/name,std::ios::binary);out<<"fixture\n";need(bool(out),"speech fixture write");
    }
  }
  const auto pocket=speech.string(),config=(speech/"config.yaml").string();
  const aii_voice_paths paths{nullptr,f.c_str(),f.c_str(),f.c_str(),f.c_str(),pocket.c_str(),config.c_str()};
  const aii_voice_speech_settings french{"alba","fr","en",.3f,20260908},english{"alba","en","en",.3f,20260908};
  aii_voice_models* owner=nullptr;aii_voice_session* session=nullptr;aii_voice_error error{};
  need(aii::voice::load_native_models(&paths,"cpu",nullptr,nullptr,0,nullptr,nullptr,&owner,&error,
    std::make_unique<Replacement>())==AII_VOICE_OK&&owner,"speech fixture load failed");
  const int first=speech_loads,asked_at_load=asked;
  need(described(readback(owner))==first,"the readback after load does not describe the loaded speech model");
  {
    // Held twice inside the change: in the release of the resident model,
    // where the owner's pointer still names it, and in the load of the next,
    // where there is none. Both are looked at before either is judged, so a
    // failure names every one of them it met.
    releasing.arm();loading.arm();
    Opening opening(owner,french);
    need(releasing.wait(),"the open did not release the resident speech model");
    const auto in_release=held_readback(owner);
    releasing.open();
    need(loading.wait(),"the open did not load the other language's speech model");
    const auto in_load=held_readback(owner);
    opening.join();
    std::string broken;
    if(asked_destroyed)broken+="a readback asked a speech model that was being released; ";
    if(asked_absent)broken+="a readback asked for a speech model while none was loaded; ";
    if(described(in_release)!=first)broken+="while the model was being released the readback was "+said(in_release)+"; ";
    if(described(in_load)!=first)broken+="while the next model was loading the readback was "+said(in_load)+"; ";
    if(!broken.empty())throw std::runtime_error(broken);
    need(opening.result==AII_VOICE_OK&&opening.session,opening.error.message);
    session=opening.session;
  }
  need(described(readback(owner))==first+1,"after a change of language the readback still describes the model before it");
  retire(session);
  // A language whose model does not load: the one before is put back, as
  // another instance, and that is the one a readback describes.
  refuse_speech=1;
  need(open_session(owner,english,&session,&error)==AII_VOICE_INVALID&&!session&&
       std::string(error.message)=="speech model for English did not load: speech load refused","a language that did not load was not refused to its session");
  need(described(readback(owner))==first+2,"after a language that did not load the readback describes a model that is gone");
  // Neither loads: no speech model is resident, and the readback is refused
  // in the words it had before this change. Speech that returns is described.
  refuse_speech=2;
  need(open_session(owner,english,&session,&error)==AII_VOICE_FAILED&&!session,"an engine without a speech model opened a session");
  const auto none=readback(owner);
  need(none.result==AII_VOICE_FAILED&&none.error=="native backend -1: TTS execution readback failed","an engine without a speech model was not refused its readback as before");
  need(open_session(owner,english,&session,&error)==AII_VOICE_OK&&session,error.message);
  need(described(readback(owner))==first+3,"after speech returned the readback does not describe its model");
  retire(session);
  // Unheld, and for a race detector more than for these assertions: one
  // thread never stops asking while languages change on another. Every
  // answer is a whole description and never an older one than the last.
  std::atomic<bool> stop{false},faulted{false};std::atomic<int> answers{0};std::string fault;int changes=0;
  {
    std::thread reader([&]{
      for(int last=0;!stop;++answers){
        const auto r=readback(owner);const int now=described(r);
        if(!now||now<last){fault="during changes of language a readback was "+said(r)+" after load "+std::to_string(last);faulted=true;return;}
        last=now;
      }
    });
    struct Stop {std::atomic<bool>& stop;std::thread& reader;~Stop(){stop=true;reader.join();}} stopping{stop,reader};
    const auto end=std::chrono::steady_clock::now()+std::chrono::seconds(4);
    while(!faulted&&(changes<40||(answers<200&&std::chrono::steady_clock::now()<end))){
      need(open_session(owner,changes++%2?english:french,&session,&error)==AII_VOICE_OK&&session,error.message);
      retire(session);
    }
  }
  if(faulted)throw std::runtime_error(fault);
  need(answers>0,"no readback was answered while languages changed");
  need(asked==asked_at_load+4+changes&&asked_absent==1&&!asked_destroyed,"a speech model was asked other than once each time one became resident");
  need(aii_voice_models_release(&owner,&error)==AII_VOICE_OK&&!owner&&!live_tts,"speech fixture release failed");
}
}
#endif
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
#if defined(__linux__) && !defined(__ANDROID__)
    execution_readback_contract(root,f);
    need(destroyed==4 && defaults==1 && !live_vad && !live_endpoint && !live_tts,"execution readback contract leaked a component");
    std::cout<<"Speech execution readback during a change of language PASS\n";
#endif
    std::cout<<"Native recognizer injection, default preservation, downstream-failure custody PASS\n";return 0;
  }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
