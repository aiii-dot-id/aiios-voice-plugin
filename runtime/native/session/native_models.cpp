#include "native_models.h"
#include "speech_languages.h"
#include "startup_trace.h"
#include "tts_phase_trace.h"
#include "../../native_asr/asr.h"
#include "../../native_vad/vad.h"
#include "../../native_endpoint/endpoint.h"
#include <atomic>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <limits>
#include <mutex>
#include <stdexcept>
#ifdef AII_MULTITALKER_ASR
#include "../../native_multitalker/recognizer.h"
#include "../../native_multitalker/onnx_separator.h"
#ifdef __APPLE__
#include "../../native_multitalker/coreml_separator.h"
#endif
#include "worker_json.h"
#include "hearing_profile.h"
#endif

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
  std::ifstream f(std::filesystem::u8path(path),std::ios::binary|std::ios::ate);
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
  size_t playback_reserve_samples() const override { return 3 * 24000; }
  // Replaced only by select(), under `control`. Whoever speaks (start, next,
  // reset) uses it outside that lock, because a cancel has to get in while
  // they compute; that holds only while configure() is called before that
  // owner exists or by that owner itself, never beside it. Nothing else
  // reads it: see `published`.
  void* model=nullptr;
  TtsPhaseTrace phase_trace;
  std::mutex control;
#if defined(__linux__) && !defined(__ANDROID__)
  // What the resident model says of where it runs, kept as text. A status
  // request reads it on the worker's control thread while a session opening
  // on another thread may be inside select(), where `model` is being
  // released or is not loaded yet: asking the model then was a refused
  // readback that failed the engine, or a read of freed memory. So the
  // model is asked by its owner alone,
  // each time one becomes resident, and a readback takes this copy under its
  // own lock: never `model`, and never `control`, which a load holds for as
  // long as it takes. While a load runs the copy is the model before it.
  std::mutex published;
  std::string execution;
  int execution_status=-1;
  void publish_execution() {
    char value[4096]{};
    const int status=nv_execution_info(model,value,sizeof value); // no model: the library's own refusal
    std::lock_guard<std::mutex> lock(published);
    execution_status=status; execution=status?"":value;
  }
  std::string execution_info() {
    std::lock_guard<std::mutex> lock(published);
    aii::voice::check(execution_status,"TTS execution readback failed");
    return execution;
  }
#else
  void publish_execution() {}
#endif
  uint64_t generation=0,client=0,cancelled=0;
  bool started=false,silent=false;
  uint32_t seed=20260908;
  // English is the model at the bound root. Another language is its own model
  // under languages/<directory>; one speech model is resident at a time.
  const std::filesystem::path english_root,english_config;
  const std::string backend;
  std::string language="en";
  SpeechReplacements replacements;
  explicit Pocket(const ModelPaths& p)
      :english_root(std::filesystem::u8path(p.pocket)),english_config(std::filesystem::u8path(p.pocket_config)),backend(p.tts_backend) {
    StartupSpan profile("load_tts");
    char error[1024]{};
    model=nv_create_bound(p.pocket.c_str(),p.pocket_config.c_str(),p.tts_backend.c_str(),4,error,sizeof error);
    if(!model) throw std::runtime_error(error);
    publish_execution();
  }
  ~Pocket() override { if(model && nv_destroy(model)) std::terminate(); }
  struct Location { std::filesystem::path root,config; };
  Location location(const SpeechLanguage& next) const {
    if(!*next.directory) return {english_root,english_config};
    const auto root=english_root/"languages"/next.directory;
    return {root,root/"config.yaml"};
  }
  static std::string read_config(const std::filesystem::path& file) {
    std::ifstream in(file,std::ios::binary); std::string bytes(65537,'\0');
    in.read(bytes.data(),std::streamsize(bytes.size()));
    if(in.bad() || !in.eof() || in.gcount()<=0 || in.gcount()>65536) throw std::runtime_error("speech model configuration read/size bound");
    bytes.resize(size_t(in.gcount())); return bytes;
  }
  void* create(const Location& at,char* error,size_t capacity) const {
    return nv_create_bound(at.root.u8string().c_str(),at.config.u8string().c_str(),backend.c_str(),4,error,capacity);
  }
  // Initialization owner only. A language that is not installed, or whose
  // configuration this engine cannot read, is refused before the resident
  // model is touched, so that session fails and the next one still speaks.
  // After that point the old model is released first (two never share the
  // device); if the new one does not load, the old one is put back and the
  // session is refused. Only when neither loads is the engine without speech,
  // which is a failure and is reported as one.
  // What a session asks for has to be on disk before anything resident is
  // touched: the language's model, tokenizer and configuration, and the one
  // preset the session names. A language still being fetched has some of its
  // files and not others; that is "not installed" for this session, not a
  // fault of the engine.
  static void require_installed(const SpeechLanguage& next,const Location& at,const std::string& voice) {
    namespace fs=std::filesystem;
    if(!fs::is_regular_file(at.root/"model.safetensors") || !fs::is_regular_file(at.root/"tokenizer.model") ||
       !fs::is_regular_file(at.config))
      throw std::invalid_argument(std::string("speaking language ")+next.label+" is not installed");
    if(voice.empty() || voice.size()>64 || voice.find_first_not_of("abcdefghijklmnopqrstuvwxyz0123456789_-")!=std::string::npos)
      throw std::invalid_argument("voice name is not a preset name");
    if(!fs::is_regular_file(at.root/"embeddings"/(voice+".safetensors")))
      throw std::invalid_argument("voice "+voice+" is not installed for "+next.label);
  }
  void select(const SpeechLanguage* next,const Location& at) {
    const std::string code=next->code;
    SpeechReplacements table;
    try { table=speech_replacements(read_config(at.config)); }
    catch(const std::exception& e) { throw std::invalid_argument(std::string("speaking language ")+next->label+" has an unreadable configuration: "+e.what()); }
    if(model) { if(nv_destroy(model)) throw std::runtime_error("speech model is busy; language unchanged"); model=nullptr; }
    char error[1024]{};
    model=create(at,error,sizeof error);
    if(model) { language=code; replacements=std::move(table); publish_execution(); return; }
    const std::string first=error;
    const auto* previous=speech_language(language);
    char second[1024]{};
    if(previous && previous!=next) model=create(location(*previous),second,sizeof second);
    publish_execution(); // the model put back is another instance; with none, a readback fails as it did
    if(!model) throw std::runtime_error(std::string("speech model for ")+next->label+" did not load ("+first+") and the previous one could not be restored ("+second+")");
    throw std::invalid_argument(std::string("speech model for ")+next->label+" did not load: "+first);
  }
  // What configure would refuse for a voice in the language already
  // loaded, from the files on disk alone: asked from the control owner
  // while a reply may be being spoken, so it takes no lock and touches no
  // model. A language is never changed this way (Session::speech). It hides
  // the backend-result helper of the same name, which this owner therefore
  // names in full (aii::voice::check).
  void check(const SpeechSettings& settings) const override {
    const auto* next=speech_language(settings.tts_language);
    if(!next) throw std::invalid_argument("unsupported speaking language");
    require_installed(*next,location(*next),settings.voice);
  }
  void configure(const SpeechSettings& settings) override {
    std::lock_guard<std::mutex> lock(control);
    if(started)throw std::runtime_error("previous TTS generation not retired");
    const auto* next=speech_language(settings.tts_language);
    if(!next) throw std::invalid_argument("unsupported speaking language");
    const auto at=location(*next);
    require_installed(*next,at,settings.voice);
    if(!model || settings.tts_language!=language) select(next,at);
    char error[1024]{};
    aii::voice::check(nv_configure_voice(model,settings.voice.c_str(),settings.temperature,error,sizeof error),error);
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
    // What the language's model never saw in training is replaced as its own
    // configuration says. A segment that was nothing else has nothing to say:
    // it completes naturally without audio instead of failing the model.
    const auto spoken=replace_speech_characters(replacements,text);
    silent=spoken.find_first_not_of(" \t\r\n")==std::string::npos;
    if(silent) return;
    char error[1024]{}; started=true;
    phase_trace.begin(id,generation);
    const auto rc=nv_start(model,generation,spoken.c_str(),seed,750,nullptr,error,sizeof error);
    phase_trace.started(rc);
    if(rc==-2) throw Cancelled("synthesis cancelled at start");
    aii::voice::check(rc,error);
  }
  std::vector<float> next() override {
    if(silent) return {};
    std::vector<float> result(120000); size_t n=0; char error[1024]{};
    const auto rc=nv_next(model,generation,result.data(),result.size(),&n,error,sizeof error);
    if(rc==-2) throw Cancelled("synthesis cancelled during inference");
    if(rc!=0 && rc!=1) aii::voice::check(rc,error);
    phase_trace.audio(n);
    result.resize(n); return result;
  }
  void reset() override {
    if(started) {
      char error[1024]{}; aii::voice::check(nv_reset(model,generation,error,sizeof error),error); started=false;
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
namespace {
std::unique_ptr<Recognizer> default_recognizer(const ModelPaths& p) {
#ifdef AII_MULTITALKER_ASR
  const auto hearing=aii::voice::wire::HearingProfile::read(p.asr_execution,std::filesystem::u8path(p.asr));
  aii::multitalker::NemotronConfig diarizer;
  diarizer.model=hearing.model;diarizer.gpu=hearing.gpu;
  // The resident path uses the same bounded, exact-mask refinement qualified
  // by the native recognizer. Its replay runs on a copy of the diarizer
  // stream, so neither an accepted nor a refused replay moves the speaker
  // memory the next utterance continues from (BOUNDED_DIARIZATION_REFINEMENT.md).
  // Legacy hearing has no compatible replay path.
  diarizer.refine_evidence=!diarizer.model.empty();
  aii::multitalker::EncoderExecution execution;execution.cuda_device=hearing.encoder_cuda;
  execution.threads=hearing.encoder_threads;
  const auto mel=floats(p.mel,128*257);
  const auto path=std::filesystem::u8path(p.asr)/"tokens.json";
  const auto raw=bytes(path.u8string(),1024*1024);
  auto tokens=aii::voice::wire::parse(std::string(raw.begin(),raw.end()));
  if(!cJSON_IsArray(tokens.get()) || cJSON_GetArraySize(tokens.get())!=1024)
    throw std::runtime_error("multitalker vocabulary geometry");
  std::vector<std::string> vocabulary;
  for(auto* token=tokens->child;token;token=token->next) {
    if(!cJSON_IsString(token) || !token->valuestring)
      throw std::runtime_error("multitalker vocabulary token");
    vocabulary.emplace_back(token->valuestring);
  }
  if(!hearing.separator_backend.empty())
    execution.weights=aii::multitalker::OnnxEncoder::load_weights(p.asr,execution);
  auto make_recognizer=[&]{return std::make_unique<aii::multitalker::Recognizer>(
      p.asr,mel.data(),mel.size(),vocabulary,diarizer,execution);};
  auto live=make_recognizer();
  if(hearing.separator_backend.empty())return live;
  std::unique_ptr<aii::multitalker::SourceSeparator> separator;
  if(hearing.separator_backend=="coreml") {
#ifdef __APPLE__
    separator=std::make_unique<aii::multitalker::CoreMLSeparator>(hearing.separator_model);
#else
    throw std::runtime_error("Core ML separation requires an Apple target");
#endif
  } else {
    const auto graph=bytes(hearing.separator_model,512u*1024*1024);
    separator=std::make_unique<aii::multitalker::OnnxSeparator>(graph.data(),graph.size(),
        hearing.separator_threads,hearing.separator_cuda);
  }
  // Separate inference context preserves the live session's diarizer history.
  // Recurrent caches and cancellation remain private. Only the stateless
  // encoder session is shared; decoder and diarizer contexts stay independent.
  return std::make_unique<aii::multitalker::SeparatingRecognizer>(
      std::move(live),make_recognizer(),std::move(separator));
#else
  return std::make_unique<Asr>(p);
#endif
}
}
struct NativeModels::Impl {
  std::unique_ptr<Recognizer> asr; VoiceVad vad; VoiceEndpoint endpoint; Pocket pocket;
  Impl(const ModelPaths& p,std::unique_ptr<Recognizer> supplied)
      :asr(supplied?std::move(supplied):default_recognizer(p)),vad(p),endpoint(p),pocket(p) {}
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
// The published copy, never the model: a status request asks this while an
// open may be replacing the model.
std::string NativeModels::tts_execution_info(){return p_->pocket.execution_info();}
#endif
}
