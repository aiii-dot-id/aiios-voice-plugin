#include "native_models.h"
#include "startup_trace.h"
#include "tts_phase_trace.h"
#include "../../native_asr/asr.h"
#include "../../native_vad/vad.h"
#include "../../native_endpoint/endpoint.h"
#include <atomic>
#include <cstring>
#include <fstream>
#include <limits>
#include <mutex>
#include <stdexcept>

extern "C" {
void* nv_create_bound(const char*,const char*,const char*,int,char*,size_t) noexcept;
int nv_configure_voice(void*,const char*,float,char*,size_t) noexcept;
int nv_start(void*,uint64_t,const char*,uint32_t,int,const char*,char*,size_t) noexcept;
int nv_next(void*,uint64_t,float*,size_t,size_t*,char*,size_t) noexcept;
int nv_cancel(void*,uint64_t) noexcept;
int nv_reset(void*,uint64_t,char*,size_t) noexcept;
int nv_destroy(void*) noexcept;
#if defined(__linux__) && !defined(__ANDROID__)
int nv_execution_info(void*,char*,size_t) noexcept;
#endif
}
namespace aii::voice {
namespace {
std::vector<char> bytes(const std::string& path,size_t limit) {
  std::ifstream f(path,std::ios::binary|std::ios::ate);
  if(!f) throw std::runtime_error("cannot open bound model: "+path);
  const auto n=f.tellg();
  if(n<=0 || uint64_t(n)>limit) throw std::runtime_error("model byte bound: "+path);
  std::vector<char> result(static_cast<size_t>(n)); f.seekg(0); f.read(result.data(),n);
  if(!f || f.peek()!=EOF) throw std::runtime_error("model read changed: "+path);
  return result;
}
std::vector<float> floats(const std::string& path,size_t count) {
  const auto data=bytes(path,count*4);
  if(data.size()%4) throw std::runtime_error("coefficient alignment");
  std::vector<float> result(data.size()/4); std::memcpy(result.data(),data.data(),data.size()); return result;
}
void check(int rc,const char* message) {
  if(rc) throw std::runtime_error("native backend "+std::to_string(rc)+": "+message);
}
void check_asr(int rc,const char* message) {
  if(rc==2) throw Cancelled("ASR cancelled");
  check(rc,message);
}
struct Asr final:Recognizer {
  AiiAsr* model=nullptr;
  AiiAsrStream* stream=nullptr;
  std::mutex lifetime;
  bool cancelled=false;
  explicit Asr(const ModelPaths& p) {
    StartupSpan profile("load_asr");
    const auto mel=floats(p.mel,100000); char error[1024]{};
    model=p.asr_execution.empty()?aii_asr_create(p.asr.c_str(),mel.data(),mel.size(),4,error,sizeof error):
      aii_asr_create_configured(p.asr.c_str(),mel.data(),mel.size(),4,p.asr_execution.c_str(),error,sizeof error);
    if(!model) throw std::runtime_error(error);
  }
  ~Asr() override { reset(); aii_asr_destroy(model); }
  std::string execution_info() const override {
    char data[4096]{},error[1024]{};size_t required=0;
    check(aii_asr_execution_info(model,data,sizeof data,&required,error,sizeof error),error);
    if(!required||required>sizeof data)throw std::runtime_error("ASR execution readback bound");
    return std::string(data,required-1);
  }
  void open() override {
    std::lock_guard<std::mutex> lock(lifetime);
    if(stream) throw std::runtime_error("previous ASR owner is not retired");
    cancelled=false;
  }
  void begin() override {
    { std::lock_guard<std::mutex> lock(lifetime); if(stream) throw std::runtime_error("ASR stream already active"); }
    char error[1024]{};
    auto* created=aii_asr_stream_create(model,error,sizeof error);
    if(!created) throw std::runtime_error(error);
    std::lock_guard<std::mutex> lock(lifetime);
    stream=created;
    if(cancelled) aii_asr_cancel(stream);
  }
  std::string result() {
    char error[1024]{},text[131072]{};
    for(;;) {
      const auto rc=aii_asr_step(stream,error,sizeof error);
      if(rc==0) break;
      if(rc==2) throw Cancelled("ASR cancelled");
      if(rc!=1) check(rc,error);
    }
    check_asr(aii_asr_result(stream,text,sizeof text,error,sizeof error),error); return text;
  }
  std::string push(const float* p,size_t n) override {
    char error[1024]{}; check_asr(aii_asr_accept(stream,p,n,error,sizeof error),error); return result();
  }
  std::string finish() override {
    char error[1024]{}; check_asr(aii_asr_finish(stream,error,sizeof error),error); return result();
  }
  void reset() override {
    AiiAsrStream* retired=nullptr;
    { std::lock_guard<std::mutex> lock(lifetime); retired=stream; stream=nullptr; }
    if(retired) {
      char error[1024]{}; const auto rc=aii_asr_stream_destroy(retired,error,sizeof error);
      if(rc) throw std::runtime_error(error);
    }
  }
  void cancel() noexcept override {
    std::lock_guard<std::mutex> lock(lifetime);
    cancelled=true; if(stream) aii_asr_cancel(stream);
  }
};
struct VoiceVad final:Vad {
  AiiVad* model=nullptr;
  std::vector<char> data;
  explicit VoiceVad(const ModelPaths& p) {
    StartupSpan profile("load_vad");
    data=bytes(p.vad,10*1024*1024);
    char error[1024]{}; model=aii_vad_create(data.data(),data.size(),error,sizeof error);
    if(!model) throw std::runtime_error(error);
  }
  ~VoiceVad() override { aii_vad_destroy(model); }
  void reset() override { char e[1024]{}; check(aii_vad_reset(model,e,sizeof e),e); }
  float score(const float* p) override {
    float value=0; char e[1024]{}; check(aii_vad_feed(model,p,512,&value,e,sizeof e),e); return value;
  }
};
struct VoiceEndpoint final:Endpoint {
  AiiEndpoint* model=nullptr;
  std::vector<char> data;
  std::vector<float> coefficients;
  std::atomic<uint64_t> counter{0};
  std::atomic<bool> cancelled{false};
  explicit VoiceEndpoint(const ModelPaths& p) {
    StartupSpan profile("load_endpoint");
    data=bytes(p.endpoint,200*1024*1024);coefficients=floats(p.coefficients,1000000);
    char error[1024]{};
    model=aii_endpoint_create(data.data(),data.size(),coefficients.data(),coefficients.size(),error,sizeof error);
    if(!model) throw std::runtime_error(error);
  }
  ~VoiceEndpoint() override { aii_endpoint_destroy(model); }
  void open() override { cancelled=false; }
  double score(uint64_t,const std::vector<float>& pcm) override {
    const uint64_t id=++counter;
    if(cancelled) throw Cancelled("endpoint cancelled");
    char error[1024]{}; double p=0;
    const auto rc=aii_endpoint_score(model,id,pcm.data(),pcm.size(),&p,nullptr,0,error,sizeof error);
    if(rc==3) throw Cancelled("endpoint cancelled");
    check(rc,error);
    return p;
  }
  void cancel() noexcept override {
    cancelled=true; aii_endpoint_cancel_through(model,counter.load());
  }
};
struct Pocket final:Synthesizer {
  void* model=nullptr;
  TtsPhaseTrace phase_trace;
  std::mutex control;
  uint64_t generation=0,client=0,cancelled=0;
  bool started=false;
  uint32_t seed=20260908;
  explicit Pocket(const ModelPaths& p) {
    StartupSpan profile("load_tts");
    char error[1024]{};
    model=nv_create_bound(p.pocket.c_str(),p.pocket_config.c_str(),p.tts_backend.c_str(),4,error,sizeof error);
    if(!model) throw std::runtime_error(error);
  }
  ~Pocket() override { if(model && nv_destroy(model)) std::terminate(); }
  void configure(const SpeechSettings& settings) override {
    std::lock_guard<std::mutex> lock(control);
    if(started)throw std::runtime_error("previous TTS generation not retired");
    char error[1024]{};
    check(nv_configure_voice(model,settings.voice.c_str(),settings.temperature,error,sizeof error),error);
    seed=settings.seed;
  }
  void open() override {
    std::lock_guard<std::mutex> lock(control);
    if(started) throw std::runtime_error("previous TTS owner is not retired");
    client=cancelled=0;
  }
  void start(uint64_t id,const std::string& text) override {
    {
      std::lock_guard<std::mutex> lock(control);
      if(id<client || started || generation==std::numeric_limits<uint64_t>::max()) throw std::runtime_error("native generation reuse/exhaustion");
      if(cancelled>=id) throw Cancelled("synthesis already cancelled");
      client=id; ++generation;
    }
    char error[1024]{}; started=true;
    phase_trace.begin(id,generation);
    const auto rc=nv_start(model,generation,text.c_str(),seed,750,nullptr,error,sizeof error);
    phase_trace.started(rc);
    if(rc==-2) throw Cancelled("synthesis cancelled at start");
    check(rc,error);
  }
  std::vector<float> next() override {
    std::vector<float> result(120000); size_t n=0; char error[1024]{};
    const auto rc=nv_next(model,generation,result.data(),result.size(),&n,error,sizeof error);
    if(rc==-2) throw Cancelled("synthesis cancelled during inference");
    if(rc!=0 && rc!=1) check(rc,error);
    phase_trace.audio(n);
    result.resize(n); return result;
  }
  void reset() override {
    if(started) {
      char error[1024]{}; check(nv_reset(model,generation,error,sizeof error),error); started=false;
      phase_trace.finish();
    }
  }
  void cancel(uint64_t id) noexcept override {
    std::lock_guard<std::mutex> lock(control);
    cancelled=std::max(cancelled,id);
    if(client && id==client) nv_cancel(model,generation);
  }
};
}
struct NativeModels::Impl {
  std::unique_ptr<Recognizer> asr; VoiceVad vad; VoiceEndpoint endpoint; Pocket pocket;
  Impl(const ModelPaths& p,std::unique_ptr<Recognizer> supplied)
      :asr(supplied?std::move(supplied):std::make_unique<Asr>(p)),vad(p),endpoint(p),pocket(p) {}
};
NativeModels::NativeModels(const ModelPaths& p):NativeModels(p,nullptr) {}
NativeModels::NativeModels(const ModelPaths& p,std::unique_ptr<Recognizer> supplied)
    :p_(std::make_unique<Impl>(p,std::move(supplied))) {}
NativeModels::~NativeModels()=default;
Recognizer& NativeModels::recognizer(){return *p_->asr;}
Vad& NativeModels::vad(){return p_->vad;}
Endpoint& NativeModels::endpoint(){return p_->endpoint;}
Synthesizer& NativeModels::synthesizer(){return p_->pocket;}
#if defined(__linux__) && !defined(__ANDROID__)
std::string NativeModels::tts_execution_info(){
  char value[4096]{};
  check(nv_execution_info(p_->pocket.model,value,sizeof value),"TTS execution readback failed");
  return value;
}
#endif
}
