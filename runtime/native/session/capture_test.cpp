#include "c_api_internal.h"
#include <algorithm>
#include <cmath>
#include <condition_variable>
#include <cstring>
#include <future>
#include <iostream>
#include <mutex>
#include <stdexcept>

namespace {
void need(bool ok,const char* why) {if(!ok)throw std::runtime_error(why);}
struct Asr:aii::voice::Recognizer {
  void begin() override {} std::string push(const float*,size_t) override {return {};}
  std::string finish() override {return {};}
  void reset() override {} void cancel() noexcept override {}
};
struct Vad:aii::voice::Vad {void reset() override {} float score(const float*) override {return 0;}};
struct Endpoint:aii::voice::Endpoint {
  double score(uint64_t,const std::vector<float>&) override {return 0;}
  void cancel() noexcept override {}
};
struct Tts:aii::voice::Synthesizer {
  void start(uint64_t,const std::string&) override {}
  std::vector<float> next() override {return {};}
  void reset() override {} void cancel(uint64_t) noexcept override {}
};
struct Owner:aii::voice::ModelOwner {
  Asr a;Vad v;Endpoint e;Tts t;
  std::mutex mutex;std::condition_variable changed;
  bool held=false,entered=false;int invalid=0;unsigned calls=0;
  aii::voice::Recognizer& recognizer() override {return a;}
  aii::voice::Vad& vad() override {return v;}
  aii::voice::Endpoint& endpoint() override {return e;}
  aii::voice::Synthesizer& synthesizer() override {return t;}
  aii_voice_readiness warm() override {return {4,1,"cpu"};}
  aii_voice_capture prepare_capture(const std::vector<float>& pcm) override {
    ++calls;
    {std::unique_lock<std::mutex> lock(mutex);entered=true;changed.notify_all();changed.wait(lock,[&]{return !held;});}
    if(invalid==5)throw std::runtime_error("injected model failure");
    aii_voice_capture r{};r.samples=pcm.size();
    std::fill_n(r.embedding_binding,64,'a');std::fill_n(r.pcm_sha256,64,'b');r.embedding[0]=1;
    if(invalid==1)++r.samples;
    if(invalid==2)r.pcm_sha256[64]='x';
    if(invalid==3)r.embedding[0]=NAN;
    if(invalid==4)r.embedding[0]=2;
    return r;
  }
};
}

int main() {
  auto owned=std::make_unique<Owner>();auto* owner=owned.get();
  auto* models=aii::voice::wrap_models(std::move(owned));
  aii_voice_error error{};aii_voice_capture result,prior;
  std::memset(&result,0x5a,sizeof result);std::memcpy(&prior,&result,sizeof prior);
  std::vector<float> pcm(32000,.25f);
  auto unchanged=[&]{return std::memcmp(&result,&prior,sizeof result)==0;};
  auto prepare=[&](size_t n){return aii_voice_prepare_capture(models,pcm.data(),n,&result,&error);};
  need(aii_voice_prepare_capture(nullptr,pcm.data(),pcm.size(),&result,&error)==AII_VOICE_INVALID&&unchanged(),"null model touched output");
  need(prepare(31919)==AII_VOICE_INVALID&&unchanged(),"short capture accepted");
  need(prepare(480001)==AII_VOICE_INVALID&&unchanged(),"long capture accepted");
  pcm[7]=NAN;need(prepare(pcm.size())==AII_VOICE_INVALID&&unchanged(),"nonfinite capture accepted");
  pcm[7]=1.01f;need(prepare(pcm.size())==AII_VOICE_INVALID&&unchanged(),"out of range capture accepted");
  pcm[7]=.25f;need(owner->calls==0,"invalid input entered the model");
  for(int fault=1;fault<=5;++fault) {
    owner->invalid=fault;
    need(prepare(pcm.size())==(fault==5?AII_VOICE_FAILED:AII_VOICE_INVALID)&&unchanged(),"invalid evidence escaped or changed output");
    aii_voice_readiness ready{};need(aii_voice_models_warm(models,&ready,&error)==AII_VOICE_OK,"failed capture leaked exclusive lease");
  }
  owner->invalid=0;owner->held=true;owner->entered=false;
  auto future=std::async(std::launch::async,[&]{return prepare(pcm.size());});
  {std::unique_lock<std::mutex> lock(owner->mutex);owner->changed.wait(lock,[&]{return owner->entered;});}
  aii_voice_session* speech=nullptr;aii_voice_readiness ready{};aii_voice_error other{};
  const auto warm=aii_voice_models_warm(models,&ready,&other);
  const auto open=aii_voice_open(models,nullptr,&speech,&other);
  const auto competing=aii_voice_prepare_capture(models,pcm.data(),pcm.size(),&prior,&other);
  const auto release=aii_voice_models_release(&models,&other);
  {std::lock_guard<std::mutex> lock(owner->mutex);owner->held=false;owner->changed.notify_all();}
  const auto completed=future.get();
  need(warm==AII_VOICE_BUSY&&open==AII_VOICE_BUSY&&competing==AII_VOICE_BUSY&&release==AII_VOICE_BUSY,"capture shared or lost model lease");
  need(completed==AII_VOICE_OK&&result.samples==pcm.size()&&result.embedding[0]==1,"capture did not publish exact completed evidence");
  need(aii_voice_open(models,nullptr,&speech,&error)==AII_VOICE_OK,"speech could not reopen after capture");
  need(prepare(pcm.size())==AII_VOICE_BUSY,"capture ran concurrently with speech");
  need(aii_voice_close(speech,1,&error)==AII_VOICE_OK,"speech abort failed");
  need(aii_voice_wait(speech,2000,&error)==AII_VOICE_OK,"speech failed to retire");
  need(aii_voice_release(&speech,&error)==AII_VOICE_OK,"speech release failed");
  need(prepare(pcm.size())==AII_VOICE_OK,"capture still required live speech");
  need(aii_voice_models_release(&models,&error)==AII_VOICE_OK&&!models,"capture leaked model ownership");
  std::cout<<"offline capture: validation, untouched output, exclusive lease, failure recovery and capture after speech close passed\n";
}
