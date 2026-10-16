// Recorded development replay through the actual packaged native C ABI.
// No annotated VAD, model substitutes, threshold override or generated answer.
#include "c_api.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
using Clock=std::chrono::steady_clock;
using namespace std::chrono_literals;
static aii_voice_error error{};
static void need(bool b,const char* why){if(!b)throw std::runtime_error(why);}
static void ok(aii_voice_result r){if(r!=AII_VOICE_OK)throw std::runtime_error(std::to_string(r)+": "+error.message);}
static std::string quote(const std::string& s){
  std::string q="\"";
  for(unsigned char c:s){if(c=='"'||c=='\\'){q+='\\';q+=c;}else if(c<32){
    const char* h="0123456789abcdef";q+="\\u00";q+=h[c>>4];q+=h[c&15];}else q+=c;}
  return q+'"';
}
static double seconds(Clock::time_point t){return std::chrono::duration<double>(Clock::now()-t).count();}
int main(int argc,char** argv){
  aii_voice_models* models=nullptr;aii_voice_session* session=nullptr;
  try{
    need(argc==11,"asr mel vad endpoint coeff tts config input-list backend first-index required");
    const auto first=std::stoull(argv[10]);need(first<10000,"bounded first index required");
    aii_voice_paths paths{argv[1],argv[2],argv[3],argv[4],argv[5],argv[6],argv[7]};
    std::ifstream list(argv[8]);need(bool(list),"input list unavailable");
    auto loading=Clock::now();
    ok(aii_voice_models_load_with_backend(&paths,argv[9],&models,&error));
    std::cout<<std::setprecision(12)<<"{\"kind\":\"models_loaded\",\"seconds\":"<<seconds(loading)<<"}\n"<<std::flush;
    std::string path;size_t cases=0,failed=0;
    while(std::getline(list,path)){
      need(!path.empty(),"empty input path");
      if(cases<first){++cases;continue;}
      std::ifstream file(path,std::ios::binary|std::ios::ate);const auto bytes=file.tellg();
      need(file && bytes>0 && bytes%4==0 && bytes<=16000*120*4,"bounded PCM required");
      std::vector<float> pcm(size_t(bytes)/4);file.seekg(0);file.read(reinterpret_cast<char*>(pcm.data()),bytes);
      need(bool(file)&&file.peek()==EOF,"PCM read changed");
      // Match the native feed contract. A normalized floating-point resampler
      // may overshoot unity; clipping changes the waveform and is not allowed.
      for(float v:pcm)need(std::isfinite(v),"invalid PCM");
      const auto original=pcm.size();
      // Explicit fixed observation tail, not a label-derived boundary.
      pcm.resize(original+48000,0);
      const auto begin=Clock::now();std::string failure;
      size_t offset=0,sequence=0;double feed_lag=0;
      std::cout<<"{\"kind\":\"case_start\",\"index\":"<<cases<<",\"source_samples\":"<<original
               <<",\"total_samples\":"<<pcm.size()<<"}\n"<<std::flush;
      try{
        aii_voice_settings settings{768,3000,.5f};
        ok(aii_voice_open(models,&settings,&session,&error));
        auto pump=[&]{
          need(seconds(begin)<double(pcm.size())/16000+30,"replay owner deadline");
          aii_voice_event e{};std::vector<char> text(262144);size_t required=0;
          for(;;){
            auto rc=aii_voice_next_event(session,&e,text.data(),text.size(),&required,&error);
            if(rc==AII_VOICE_AGAIN)break;ok(rc);need(e.sequence==++sequence,"event sequence changed");
            std::cout<<"{\"kind\":\"event\",\"index\":"<<cases<<",\"seconds\":"<<seconds(begin)
              <<",\"event\":"<<quote(e.kind)<<",\"sequence\":"<<e.sequence<<",\"turn\":"<<e.turn
              <<",\"start\":"<<e.start<<",\"end\":"<<e.end<<",\"text\":"<<quote(text.data())<<"}\n"<<std::flush;
          }
          aii_voice_snapshot s{};ok(aii_voice_status(session,&s,&error));need(!*s.error,s.error);return s;
        };
        // Deliver each complete audio block only at its capture time. Absolute
        // pacing retains lateness instead of slowing the source to hide it.
        for(;offset<pcm.size();){
          const auto count=std::min(size_t(512),pcm.size()-offset);
          const auto due=begin+std::chrono::microseconds((offset+count)*1000000/16000);
          while(Clock::now()<due){pump();std::this_thread::sleep_for(1ms);}
          auto rc=aii_voice_feed(session,offset,pcm.data()+offset,count,&error);
          if(rc==AII_VOICE_AGAIN){pump();std::this_thread::sleep_for(1ms);continue;}
          ok(rc);feed_lag=std::max(feed_lag,seconds(due));offset+=count;pump();
        }
        ok(aii_voice_finish_input(session,pcm.size(),&error));
        while(!pump().input_finished)std::this_thread::sleep_for(1ms);
        const auto final=pump();need(final.recognized==pcm.size(),"input tail was lost");
        ok(aii_voice_close(session,0,&error));ok(aii_voice_wait(session,3000,&error));pump();
      }catch(const std::exception& e){
        failure=e.what();++failed;
        if(session){ok(aii_voice_close(session,1,&error));ok(aii_voice_wait(session,3000,&error));}
      }
      if(session)ok(aii_voice_release(&session,&error));
      std::cout<<"{\"kind\":\"case_end\",\"index\":"<<cases<<",\"seconds\":"<<seconds(begin)
        <<",\"admitted_samples\":"<<offset<<",\"maximum_feed_lag_seconds\":"<<feed_lag
        <<",\"retired\":true,\"error\":"<<quote(failure)<<"}\n"<<std::flush;++cases;
    }
    need(list.eof()&&cases>0,"input list failed");ok(aii_voice_models_release(&models,&error));
    std::cout<<"{\"kind\":\"complete\",\"cases\":"<<cases<<",\"failed\":"<<failed<<",\"models_retired\":true}\n";
    return failed?1:0;
  }catch(const std::exception& e){
    std::cerr<<e.what()<<'\n';
    if(session){aii_voice_close(session,1,&error);aii_voice_wait(session,3000,&error);aii_voice_release(&session,&error);}
    if(models)aii_voice_models_release(&models,&error);return 2;
  }
}
