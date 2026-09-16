// Recorded long speech and long synthesis, with real native models. This is
// a qualification driver, not a transport or a physical playback claim.
#include "native_models.h"
#include "text.h"
#include <atomic>
#include <chrono>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <thread>
using namespace aii::voice;
using Clock=std::chrono::steady_clock;
using namespace std::chrono_literals;
double elapsed(Clock::time_point t){return std::chrono::duration<double>(Clock::now()-t).count();}
void check(bool ok,const char* why){if(!ok)throw std::runtime_error(why);}
std::string quoted(const std::string& s) {
  std::string q="\"";
  for(unsigned char c:s) {
    if(c=='"'||c=='\\'){q+='\\';q+=c;}
    else if(c<32){const char* h="0123456789abcdef";q+="\\u00";q+=h[c>>4];q+=h[c&15];}
    else q+=c;
  }
  return q+'"';
}
std::string read(const char* p,size_t bound) {
  std::ifstream f(p,std::ios::binary|std::ios::ate);const auto n=f.tellg();
  check(bool(f)&&n>0&&uint64_t(n)<=bound,"bounded proof input required");
  std::string data(static_cast<size_t>(n),'\0');f.seekg(0);f.read(data.data(),n);
  check(bool(f)&&f.peek()==EOF,"proof input changed");return data;
}
struct Observed final:Synthesizer {
  Synthesizer& inner;
  std::atomic<size_t> starts{0};std::atomic<bool> in_next{false};
  explicit Observed(Synthesizer& s):inner(s){}
  void open()override{inner.open();starts=0;}
  void start(uint64_t id,const std::string& t)override{inner.start(id,t);++starts;}
  std::vector<float> next()override {
    in_next=true;
    try{auto v=inner.next();in_next=false;return v;}
    catch(...){in_next=false;throw;}
  }
  void reset()override{inner.reset();}
  void cancel(uint64_t id)noexcept override{inner.cancel(id);}
};
int main(int argc,char** argv) {
  try {
    check(argc==11,"asr mel vad endpoint coeff pocket config pcm text output required");
    auto raw=read(argv[8],16000*90*4),text=read(argv[9],32000);
    check(raw.size()%4==0,"float PCM required");
    std::vector<float> input(raw.size()/4);std::memcpy(input.data(),raw.data(),raw.size());
    check(input.size()>16000*60,"proof must cross the former minute boundary");
    const auto parts=split_text(text);check(parts.size()>2,"multi-segment proof text required");
    const std::filesystem::path out(argv[10]);check(std::filesystem::create_directory(out),"fresh output required");
    ModelPaths paths{argv[1],argv[2],argv[3],argv[4],argv[5],argv[6],argv[7]};
    const auto begin=Clock::now();NativeModels models(paths);Observed tts(models.synthesizer());
    auto deadline=[&]{check(elapsed(begin)<240,"continuous proof deadline");};
    // Direct model invocation is the PCM oracle; the session adds only
    // queueing/segmentation, not its own synthesis or audio rewriting.
    {
      std::ofstream reference(out/"reference.f32",std::ios::binary);uint64_t samples=0,id=0;
      auto& synth=models.synthesizer();synth.open();
      for(const auto& part:parts) {
        const auto segment=strip_text(part);if(segment.empty())continue;
        synth.start(++id,segment);uint64_t count=0;
        for(;;){deadline();auto pcm=synth.next();if(pcm.empty())break;
          count+=pcm.size();reference.write(reinterpret_cast<char*>(pcm.data()),pcm.size()*4);}
        check(count>0,"empty direct reference segment");synth.reset();samples+=count;
        std::cout<<"{\"kind\":\"reference_segment\",\"segment\":"<<id<<",\"samples\":"<<count
          <<",\"text\":"<<quoted(segment)<<"}\n"<<std::flush;
      }
      check(bool(reference)&&samples>24000*60,"long reference must exceed sixty seconds");
      std::cout<<"{\"kind\":\"reference_complete\",\"samples\":"<<samples<<"}\n"<<std::flush;
    }
    Session session(models.recognizer(),models.vad(),models.endpoint(),tts,Settings{5000,.5f});
    uint64_t delivered[4]={};bool ended[4]={};bool fenced=false;
    std::ofstream full(out/"continuous.f32",std::ios::binary),cancelled(out/"cancelled.f32",std::ios::binary),recovery(out/"recovery.f32",std::ios::binary);
    size_t queue_peak=0;
    auto pump=[&]{
      deadline();const auto state=session.status();check(state.error.empty(),state.error.c_str());
      queue_peak=std::max(queue_peak,state.queued_audio_samples);
      Event e;while(session.event(e))std::cout<<"{\"kind\":"<<quoted(e.kind)<<",\"sequence\":"<<e.sequence
        <<",\"generation\":"<<e.generation<<",\"start\":"<<e.start<<",\"end\":"<<e.end
        <<",\"text\":"<<quoted(e.text)<<"}\n"<<std::flush;
      Audio a;while(session.audio(a)) {
        check(a.generation>=1&&a.generation<=3,"output generation changed");
        check(a.start==delivered[a.generation],"output clock gap/overlap");
        check(!ended[a.generation],"output revived after END");
        if(a.end)ended[a.generation]=true;
        else {
          check(!(fenced&&a.generation==2),"PCM crossed cancellation fence");
          auto& file=a.generation==1?full:(a.generation==2?cancelled:recovery);
          file.write(reinterpret_cast<char*>(a.pcm.data()),a.pcm.size()*4);check(bool(file),"sink write failed");
          delivered[a.generation]+=a.pcm.size();
        }
      }
    };
    // A real VAD/recognizer sees one continuous capture, never an invented
    // boundary at sixty seconds. Unpaced input is throughput, not latency.
    for(size_t offset=0;offset<input.size();) {
      const auto n=std::min(size_t(512),input.size()-offset);
      if(session.feed(offset,input.data()+offset,n))offset+=n;
      pump();std::this_thread::sleep_for(1ms);
    }
    session.finish_input(input.size());
    while(!session.status().input_finished){pump();std::this_thread::sleep_for(1ms);}pump();
    check(session.status().recognized==input.size(),"continuous input lost its tail");
    session.synthesize(1,text);
    while(!ended[1]){pump();std::this_thread::sleep_for(1ms);}pump();
    auto complete=session.status();
    check(complete.completed_segments==complete.synthesis_segments&&tts.starts==complete.synthesis_segments,"a long segment was omitted");
    check(delivered[1]>24000*60,"continuous reply ended at the old cap");
    session.playback(1,delivered[1],true,false);
    const auto before=tts.starts.load();session.synthesize(2,text);
    while(!(tts.starts>=before+2&&tts.in_next)){pump();std::this_thread::sleep_for(1ms);}
    const auto stopping=Clock::now();session.interrupt(2);fenced=true;const double admission=elapsed(stopping);
    while(!ended[2]){pump();std::this_thread::sleep_for(1ms);}pump();
    const auto interrupted=session.status();
    check(interrupted.completed_segments<interrupted.synthesis_segments,"cancellation did not stop a later segment");
    check(tts.starts==before+2,"cancellation started a third segment");
    session.playback(2,delivered[2],true,true);
    session.synthesize(3,"The opening words were retained, and this is the complete recovery reply.");
    while(!ended[3]){pump();std::this_thread::sleep_for(1ms);}pump();
    session.close(false);check(!session.wait_closed(50),"drain completed without the final receipt");
    session.playback(3,delivered[3],true,false);check(session.wait_closed(2000),"model owners did not retire");pump();
    check(queue_peak<=120000,"output queue exceeded session budget");
    std::cout<<"{\"kind\":\"complete\",\"input_samples\":"<<input.size()<<",\"output_samples\":"<<delivered[1]
      <<",\"completed_segments\":"<<complete.completed_segments<<",\"cancel_completed_segments\":"<<interrupted.completed_segments
      <<",\"cancel_started_segments\":"<<tts.starts.load()-before-1<<",\"cancel_admission_seconds\":"<<admission
      <<",\"recovery_samples\":"<<delivered[3]<<",\"queue_peak_samples\":"<<queue_peak
      <<",\"retired\":true,\"simulated_sink\":true,\"seconds\":"<<elapsed(begin)<<"}\n";
  }catch(const std::exception& e){std::cerr<<"continuous proof failed: "<<e.what()<<'\n';return 1;}
}
