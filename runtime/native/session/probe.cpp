// Real native models, recorded input, simulated sink receipts. No Python in
// this process, no capture/playback devices, no expected transcript input.
#include "native_models.h"
#include <chrono>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <thread>
using namespace aii::voice;
using Clock=std::chrono::steady_clock;
double seconds(Clock::time_point begin){return std::chrono::duration<double>(Clock::now()-begin).count();}
std::string quote(const std::string& text) {
  std::string result="\"";
  for(unsigned char c:text) {
    if(c=='"' || c=='\\') {result+='\\';result+=c;}
    else if(c<32) {const char* hex="0123456789abcdef";result+="\\u00";result+=hex[c>>4];result+=hex[c&15];}
    else result+=c;
  }
  return result+'"';
}
std::vector<float> read(const std::string& path) {
  std::ifstream file(path,std::ios::binary|std::ios::ate);
  const auto bytes=file.tellg();
  if(!file || bytes<=0 || bytes%4 || bytes>16000*30*4) throw std::runtime_error("bounded raw float PCM required");
  std::vector<float> pcm(static_cast<size_t>(bytes)/4);file.seekg(0);file.read(reinterpret_cast<char*>(pcm.data()),bytes);
  if(!file) throw std::runtime_error("PCM read failed");return pcm;
}
int main(int argc,char** argv) {
  try {
    if(argc!=12) throw std::runtime_error("asr mel vad endpoint coeff pocket config pcm output packet pause required");
    ModelPaths paths{argv[1],argv[2],argv[3],argv[4],argv[5],argv[6],argv[7]};
    auto input=read(argv[8]); const auto input_original=input.size(); input.resize(input.size()+32000,0);
    const auto packet=std::stoul(argv[10]); const auto pause=std::stoul(argv[11]);
    if(packet==0 || packet>32768) throw std::runtime_error("packet bound");
    std::filesystem::path output(argv[9]);
    if(!std::filesystem::create_directory(output)) throw std::runtime_error("fresh output directory required");
    const auto begin=Clock::now(); NativeModels models(paths);
    std::cout<<"{\"kind\":\"ready\",\"seconds\":"<<seconds(begin)<<"}\n"<<std::flush;
    Session session(models.recognizer(),models.vad(),models.endpoint(),models.synthesizer(),Settings{uint32_t(pause),.5f});
    const auto start=Clock::now(); bool interrupted=false,first=false,final=false;
    uint64_t delivered[3]={0,0,0}; bool ended[3]={false,false,false};
    std::ofstream wave1(output/"interrupted.f32",std::ios::binary),wave2(output/"recovery.f32",std::ios::binary);
    auto pump=[&] {
      const auto state=session.status();
      if(!state.error.empty()) throw std::runtime_error(state.error);
      if(seconds(start)>90) throw std::runtime_error("native proof deadline");
      Event e;
      while(session.event(e)) {
        interrupted|=e.kind=="interruption_requested";
        final|=e.kind=="transcript_final";
        std::cout<<"{\"kind\":"<<quote(e.kind)<<",\"sequence\":"<<e.sequence<<",\"generation\":"<<e.generation
          <<",\"turn\":"<<e.turn<<",\"start\":"<<e.start<<",\"end\":"<<e.end
          <<",\"text\":"<<quote(e.text)<<",\"track\":"<<quote(e.track)<<",\"seconds\":"<<seconds(start)<<"}\n"<<std::flush;
      }
      Audio audio;
      while(session.audio(audio)) {
        if(audio.generation<1 || audio.generation>2 || audio.start!=delivered[audio.generation])
          throw std::runtime_error("output generation/clock changed");
        if(audio.end) ended[audio.generation]=true;
        else {
          if(interrupted && audio.generation==1) throw std::runtime_error("stale audio crossed fence");
          if(!first) {first=true;std::cout<<"{\"kind\":\"first_pcm\",\"seconds\":"<<seconds(start)<<"}\n"<<std::flush;}
          auto& out=audio.generation==1?wave1:wave2;
          out.write(reinterpret_cast<const char*>(audio.pcm.data()),audio.pcm.size()*4);
          if(!out) throw std::runtime_error("proof sink refused output");
          delivered[audio.generation]+=audio.pcm.size();
        }
      }
    };
    session.synthesize(1,"The local voice platform is speaking now. Please interrupt this sentence while it continues to speak.");
    while(!first) {pump();std::this_thread::sleep_for(std::chrono::milliseconds(1));}
    // Pace capture in the model clock; transport batches vary, silence pause
    // and pre-roll must not. Both models run while VAD independently fences.
    const auto input_start=Clock::now();
    for(size_t offset=0;offset<input.size();) {
      const size_t count=std::min<size_t>(packet,input.size()-offset);
      while(!session.feed(offset,input.data()+offset,count)) {pump();std::this_thread::sleep_for(std::chrono::milliseconds(1));}
      offset+=count;
      while(seconds(input_start)<double(offset)/16000) {pump();std::this_thread::sleep_for(std::chrono::milliseconds(1));}
    }
    session.finish_input(input.size());
    while(!session.status().input_finished || session.status().synthesizing || !ended[1]) {
      pump();std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    pump();
    if(!interrupted || !final || !delivered[1]) throw std::runtime_error("speech interruption/recognition was not exercised");
    session.playback(1,delivered[1],true,true); // simulated sink, NOT physical playback
    session.synthesize(2,"The opening words were retained, and this is the complete recovery reply.");
    while(session.status().synthesizing || !ended[2]) {pump();std::this_thread::sleep_for(std::chrono::milliseconds(1));}
    pump();session.close(false);
    if(session.wait_closed(50)) throw std::runtime_error("drain completed without recovery receipt");
    session.playback(2,delivered[2],true,false);
    if(!session.wait_closed(2000)) throw std::runtime_error("native owners did not retire");
    pump();
    if(session.status().recognized!=input.size()) throw std::runtime_error("input completion cutoff differs");
    std::cout<<"{\"kind\":\"complete\",\"input_samples\":"<<input.size()<<",\"fixture_samples\":"<<input_original
      <<",\"interrupted_samples\":"<<delivered[1]<<",\"recovery_samples\":"<<delivered[2]
      <<",\"retired\":true,\"simulated_sink\":true,\"seconds\":"<<seconds(start)<<"}\n";
    // Reuse the resident models across Abort, including restarted caller IDs.
    // Backend generations must remain monotonic even when session IDs restart.
    {
      Session aborted(models.recognizer(),models.vad(),models.endpoint(),models.synthesizer());
      aborted.synthesize(1,"A later session starts and is then aborted.");
      if(!aborted.feed(0,input.data(),16000)) throw std::runtime_error("Abort probe input refused");
      Audio a;
      while(!aborted.audio(a)) {
        if(!aborted.status().error.empty()) throw std::runtime_error(aborted.status().error);
        if(seconds(start)>100) throw std::runtime_error("Abort probe deadline");
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
      }
      const auto cancelling=Clock::now();aborted.close(true);
      const double admission=seconds(cancelling);
      if(!aborted.wait_closed(2000) || !aborted.status().error.empty()) throw std::runtime_error("Abort owner failure");
      std::cout<<"{\"kind\":\"abort_retired\",\"admission_seconds\":"<<admission<<",\"retirement_seconds\":"<<seconds(cancelling)<<"}\n";
    }
    {
      Session reopened(models.recognizer(),models.vad(),models.endpoint(),models.synthesizer());
      std::ofstream out(output/"reopened.f32",std::ios::binary);
      uint64_t rendered=0;bool end=false;std::string final_text;
      auto drain=[&]{
        if(!reopened.status().error.empty()) throw std::runtime_error(reopened.status().error);
        if(seconds(start)>120) throw std::runtime_error("reopen proof deadline");
        Event e;while(reopened.event(e)) if(e.kind=="transcript_final") final_text+=e.text;
        Audio a;while(reopened.audio(a)) {
          if(a.end)end=true;
          else {out.write(reinterpret_cast<char*>(a.pcm.data()),a.pcm.size()*4);rendered+=a.pcm.size();}
        }
      };
      for(size_t offset=0;offset<input.size();) {
        const auto n=std::min(size_t(241),input.size()-offset);
        if(reopened.feed(offset,input.data()+offset,n))offset+=n;
        drain();std::this_thread::sleep_for(std::chrono::milliseconds(1));
      }
      reopened.finish_input(input.size());
      while(!reopened.status().input_finished){drain();std::this_thread::sleep_for(std::chrono::milliseconds(1));}
      drain();reopened.synthesize(1,"The opening words were retained, and this is the complete recovery reply.");
      while(!end){drain();std::this_thread::sleep_for(std::chrono::milliseconds(1));}
      reopened.playback(1,rendered,true,false);reopened.close(false);
      if(!reopened.wait_closed(2000) || !out)throw std::runtime_error("reopen failed");
      std::cout<<"{\"kind\":\"reopened\",\"text\":"<<quote(final_text)<<",\"samples\":"<<rendered<<",\"retired\":true}\n";
    }
  } catch(const std::exception& e) { std::cerr<<"native composition failed: "<<e.what()<<'\n'; return 1; }
}
