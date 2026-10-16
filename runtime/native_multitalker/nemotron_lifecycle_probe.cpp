#include "nemotron.h"
#include <nemo_speech/diar.h>
#include <algorithm>
#include <chrono>
#include <fstream>
#include <iostream>
#include <stdexcept>

using aii::multitalker::Nemotron;
void check(bool b,const char* why){if(!b)throw std::runtime_error(why);}
template<class F>void refuses(F f){bool failed=false;try{f();}catch(const std::exception&){failed=true;}check(failed,"lifecycle accepted invalid operation");}
void ok(nemo_speech_asr_status s){if(s!=NEMO_SPEECH_ASR_OK)throw std::runtime_error(nemo_speech_asr_last_error());}
std::vector<float> retained(const nemo_speech_diar_stream* s) {
  std::vector<float> p(size_t(nemo_speech_diar_frame_count(s)-nemo_speech_diar_frame_probs_start(s))*8);
  if(!p.empty())ok(nemo_speech_diar_frame_probs(s,p.data(),p.size()));
  return p;
}
void feed(nemo_speech_diar_stream* s,const float* pcm,size_t n) {
  for(size_t p=0;p<n;p+=997)ok(nemo_speech_diar_stream_push_f32(s,pcm+p,std::min(size_t(997),n-p),16000));
}
// C API stream copy: identical later outputs for identical input, from a
// mid-utterance and a finished stream, and no effect of a copy on its source.
void stream_copy(const char* path,int gpu,const std::vector<float>& pcm) {
  nemo_speech_diar_model_config config{};
  config.size=sizeof(config);config.model_path=path;config.gpu=gpu;
  config.preset="v3-streaming";config.left_context_frames=-1;
  nemo_speech_diar_model* model=nullptr;ok(nemo_speech_diar_create(&config,&model));
  nemo_speech_diar_stream *source=nullptr,*copy=nullptr,*later=nullptr,*offline=nullptr,*result=nullptr;
  try {
    const size_t half=pcm.size()*7/10;
    ok(nemo_speech_diar_stream_open(model,&source));feed(source,pcm.data(),half);
    ok(nemo_speech_diar_stream_clone(source,&copy));
    check(nemo_speech_diar_frame_count(copy)==nemo_speech_diar_frame_count(source)&&
          retained(copy)==retained(source),"copy differs from its source");
    const auto frames=nemo_speech_diar_frame_count(source);const auto before=retained(source);
    feed(copy,pcm.data()+half,pcm.size()-half);ok(nemo_speech_diar_stream_finish(copy));
    check(nemo_speech_diar_frame_count(copy)>frames,"copy did not advance");
    check(nemo_speech_diar_frame_count(source)==frames&&retained(source)==before,"copy changed its source");
    feed(source,pcm.data()+half,pcm.size()-half);ok(nemo_speech_diar_stream_finish(source));
    check(retained(source)==retained(copy),"copy and source diverged on the same input");
    // A finished copy continues the same speaker memory as its source.
    ok(nemo_speech_diar_stream_clone(source,&later));
    for(auto* s:{source,later}){ok(nemo_speech_diar_stream_next_utterance(s));feed(s,pcm.data(),pcm.size());ok(nemo_speech_diar_stream_finish(s));}
    check(retained(source)==retained(later),"continued copy diverged");
    // The source can retire first; the model still owns the copy's weights.
    nemo_speech_diar_stream_close(source);source=nullptr;
    ok(nemo_speech_diar_stream_next_utterance(later));feed(later,pcm.data(),std::min(pcm.size(),size_t(16000)));
    ok(nemo_speech_diar_offline_f32(model,pcm.data(),std::min(pcm.size(),size_t(16000*30)),16000,&offline));
    ok(nemo_speech_diar_stream_clone(offline,&result));
    check(nemo_speech_diar_frame_count(result)==nemo_speech_diar_frame_count(offline)&&
          retained(result)==retained(offline),"offline copy differs");
    nemo_speech_diar_stream* none=nullptr;
    check(nemo_speech_diar_stream_clone(nullptr,&none)!=NEMO_SPEECH_ASR_OK&&!none,"null copy admitted");
  } catch(...) {
    for(auto* s:{source,copy,later,offline,result})nemo_speech_diar_stream_close(s);
    nemo_speech_diar_destroy(model);throw;
  }
  for(auto* s:{source,copy,later,offline,result})nemo_speech_diar_stream_close(s);
  nemo_speech_diar_destroy(model);
}
int main(int argc,char** argv){try{
  check(argc==4,"model, gpu, float PCM required");
  std::ifstream f(argv[3],std::ios::binary|std::ios::ate);auto n=f.tellg();
  check(f&&n>0&&n%4==0&&n<=16000*60*4,"PCM extent");
  std::vector<float> pcm(size_t(n)/4);f.seekg(0);
  check(bool(f.read(reinterpret_cast<char*>(pcm.data()),n)),"PCM read");
  stream_copy(argv[1],std::stoi(argv[2]),pcm);
  Nemotron diar({argv[1],std::stoi(argv[2])});
  refuses([&]{diar.reset(true);});
  auto run=[&](bool discard,size_t repeats){
    std::vector<float> result;uint64_t consumed=0,peak=0;
    for(size_t i=0;i<repeats;++i)for(size_t p=0;p<pcm.size();p+=997){
      diar.push(pcm.data()+p,std::min(size_t(997),pcm.size()-p));
      peak=std::max(peak,diar.retained_frames());
      const auto end=diar.frames();
      if(end>consumed){auto values=diar.range(consumed,end-consumed);result.insert(result.end(),values.begin(),values.end());}
      consumed=end;if(discard)diar.discard_before(end);
    }
    diar.finish();auto end=diar.frames();
    if(end>consumed){auto values=diar.range(consumed,end-consumed);result.insert(result.end(),values.begin(),values.end());}
    if(discard){diar.discard_before(end);check(diar.retained_frames()==0,"output history retained");check(peak<=512,"native output buffer grew");}
    refuses([&]{diar.push(pcm.data(),1);});refuses([&]{diar.finish();});
    return result;
  };
  diar.reset(false);refuses([&]{diar.reset(true);});const auto baseline=run(false,1);
  diar.reset(false);const auto bounded=run(true,1);check(baseline==bounded,"discard changed predictions");
  diar.reset(true);const auto continuation=run(true,1);check(!continuation.empty(),"continued utterance empty");
  diar.reset(false);check(run(true,1)==baseline,"new session leaked speaker memory");
  // Ten minutes of audio through the real model, unpaced. This is a bounded
  // memory/lifecycle check, explicitly not ten minutes of wall-clock soak.
  const size_t repeats=(16000*600+pcm.size()-1)/pcm.size();
  diar.reset(false);const auto began=std::chrono::steady_clock::now();
  auto sustained=run(true,repeats);
  std::cout<<"{\"passed\":true,\"audio_samples\":"<<pcm.size()*repeats
           <<",\"frames\":"<<sustained.size()/8<<",\"seconds\":"
           <<std::chrono::duration<double>(std::chrono::steady_clock::now()-began).count()
           <<",\"new_session_isolation\":true,\"discard_parity\":true,\"stream_copy_parity\":true"
           <<",\"copy_isolation\":true,\"real_time_soak\":false}\n";
  return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
