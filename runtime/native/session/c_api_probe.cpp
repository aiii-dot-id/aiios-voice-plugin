// Real model gate through the C ABI. No C++ model/session objects are exposed
// to this caller. PCM receipts here are simulated, never physical playback.
#include "c_api.h"
#include <algorithm>
#include <chrono>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
using Clock=std::chrono::steady_clock;
void need(bool value,const char* reason) { if(!value)throw std::runtime_error(reason); }
aii_voice_error error{};
void ok(aii_voice_result r) { if(r!=AII_VOICE_OK)throw std::runtime_error(std::to_string(r)+": "+error.message); }
static std::string bounded_file(const char* path,size_t limit) {
  std::ifstream f(path,std::ios::binary|std::ios::ate);const auto n=f.tellg();
  need(f && n>0 && uint64_t(n)<=limit,"bounded UID document required");
  std::string data(size_t(n),'\0');f.seekg(0);f.read(data.data(),n);need(bool(f),"UID document read");return data;
}
static aii_voice_result fixture_snapshot(void* context,char* out,size_t capacity,size_t* written) {
  const auto& bytes=*static_cast<const std::string*>(context);
  if(capacity<bytes.size())return AII_VOICE_CAPACITY;
  std::memcpy(out,bytes.data(),bytes.size());*written=bytes.size();return AII_VOICE_OK;
}
std::string quote(const std::string& text) {
  std::string out="\"";
  for(unsigned char c:text) { if(c=='"'||c=='\\'){out+='\\';out+=c;}
    else if(c<32){const char* h="0123456789abcdef";out+="\\u00";out+=h[c>>4];out+=h[c&15];}else out+=c; }
  return out+'"';
}
int main(int argc,char** argv) {
  aii_voice_models* models=nullptr;aii_voice_session* s=nullptr;
  try {
    need(argc==10 || argc==11 || argc==14,"asr mel vad endpoint coeff pocket config fixture output [cpu|vulkan [uid policy snapshot]] required");
    aii_voice_paths paths{argv[1],argv[2],argv[3],argv[4],argv[5],argv[6],argv[7]};
    std::ifstream file(argv[8],std::ios::binary|std::ios::ate);auto bytes=file.tellg();
    need(file && bytes>0 && bytes%4==0 && bytes<=16000*30*4,"bounded fixture required");
    std::vector<float> input(size_t(bytes)/4+32000,0);file.seekg(0);file.read(reinterpret_cast<char*>(input.data()),bytes);need(bool(file),"fixture read");
    const std::filesystem::path out(argv[9]);need(std::filesystem::create_directory(out),"fresh output required");
    const char* backend=argc>=11?argv[10]:"cpu";
    const bool with_uid=argc==14;
    const std::string policy=with_uid?bounded_file(argv[12],4096):"";
    const std::string snapshot=with_uid?bounded_file(argv[13],8u<<20):"";
    const auto started=Clock::now();
    if(with_uid)ok(aii_voice_models_load_with_uid(&paths,backend,argv[11],policy.data(),policy.size(),fixture_snapshot,
      const_cast<std::string*>(&snapshot),&models,&error));
    else ok(aii_voice_models_load_with_backend(&paths,backend,&models,&error));
    const auto load_seconds=std::chrono::duration<double>(Clock::now()-started).count();
    ok(aii_voice_open(models,nullptr,&s,&error));
    double first_pcm_seconds=0,recovery_first_pcm_seconds=0,recovery_seconds=0;
    Clock::time_point synth_started=Clock::now();
    std::ofstream recovery(out/"recovery.f32",std::ios::binary);
    std::vector<float> pcm(120000);std::vector<char> text(131072);
    uint64_t delivered[3]{},sequence=0,final_sequence=0;bool ended[3]{},fenced=false;
    unsigned finals=0,finished=0,observations=0;std::string transcript;
    auto status=[&]{aii_voice_snapshot v{};ok(aii_voice_status(s,&v,&error));need(!*v.error,v.error);return v;};
    auto pump=[&]{
      need(Clock::now()-started<std::chrono::seconds(120),"C ABI real-model deadline");status();
      aii_voice_event e{};size_t n=0;
      for(;;) {
        uint64_t reference=0;
        const auto r=aii_voice_next_event_with_reference(s,&e,&reference,text.data(),text.size(),&n,&error);if(r==AII_VOICE_AGAIN)break;ok(r);
        need(e.sequence==++sequence,"event clock differs");
        if(std::string(e.kind)=="transcript_final"){++finals;transcript=text.data();final_sequence=e.sequence;}
        if(std::string(e.kind)=="speaker_observation") {
          need(with_uid && reference==final_sequence && reference!=0,"speaker observation lost final identity");
          need(std::strstr(text.data(),"\"outcome\":\"unknown\"") && std::strstr(text.data(),"\"reason\":\"no_enrollments\""),
            "empty fixture profile did not produce measured unknown UID");++observations;
        }
        if(std::string(e.kind)=="input_finished"){++finished;need(finals==1 && e.start==input.size() && e.end==input.size(),"Finish ordering/cutoff");}
        std::cout<<"{\"kind\":"<<quote(e.kind)<<",\"sequence\":"<<e.sequence<<",\"start\":"<<e.start<<",\"end\":"<<e.end
          <<",\"generation\":"<<e.generation<<",\"refers_to\":"<<reference<<",\"text\":"<<quote(text.data())<<"}\n"<<std::flush;
      }
      aii_voice_audio a{};
      for(;;) {
        const auto r=aii_voice_next_audio(s,&a,pcm.data(),pcm.size(),&n,&error);if(r==AII_VOICE_AGAIN)break;ok(r);
        need(a.generation>=1 && a.generation<=2 && a.start==delivered[a.generation],"audio identity/clock differs");
        if(a.end){ended[a.generation]=true;continue;}
        need(!fenced || a.generation!=1,"PCM escaped stop fence");delivered[a.generation]+=n;
        if(a.generation==1 && !first_pcm_seconds)first_pcm_seconds=std::chrono::duration<double>(Clock::now()-synth_started).count();
        if(a.generation==2 && !recovery_first_pcm_seconds)recovery_first_pcm_seconds=std::chrono::duration<double>(Clock::now()-synth_started).count();
        if(a.generation==2) {recovery.write(reinterpret_cast<char*>(pcm.data()),n*4);need(bool(recovery),"sink write");}
      }
    };
    auto wait=[&](auto done){while(!done()){pump();std::this_thread::sleep_for(std::chrono::milliseconds(1));}pump();};
    const std::string reply="The local voice platform is speaking now. Please interrupt this sentence while it continues to speak.";
    synth_started=Clock::now();ok(aii_voice_synthesize(s,1,reply.data(),reply.size(),&error));wait([&]{return delivered[1]>0;});
    need(status().synthesizing,"control did not exercise active inference");
    auto t=Clock::now();ok(aii_voice_stop_playback(s,1,&error));fenced=true;
    const auto stop_us=std::chrono::duration<double,std::micro>(Clock::now()-t).count();
    aii_voice_generation g{};ok(aii_voice_generation_status(s,1,&g,&error));need(g.fenced && !g.cancelled,"stop cancelled compute");
    ok(aii_voice_playback(s,1,delivered[1],1,1,&error));
    t=Clock::now();ok(aii_voice_cancel_synthesis(s,1,&error));
    const auto cancel_us=std::chrono::duration<double,std::micro>(Clock::now()-t).count();
    wait([&]{return !status().synthesizing && ended[1];});
    ok(aii_voice_generation_status(s,1,&g,&error));need(g.receipt && g.cancelled && g.retired,"early receipt blocked cancellation");
    for(size_t offset=0;offset<input.size();) {
      const auto n=std::min(size_t(241),input.size()-offset);
      if(offset+n==input.size())ok(aii_voice_finish_input(s,input.size(),&error)); // control outruns final audio packet
      const auto r=aii_voice_feed(s,offset,input.data()+offset,n,&error);
      if(r==AII_VOICE_OK)offset+=n;else need(r==AII_VOICE_AGAIN,"input admission failed");
      pump();std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    wait([&]{return status().input_finished;});need(finals==1 && finished==1,"one complete transcript required");
    if(with_uid) {
      wait([&]{return observations==1;});
      uint64_t eligible[16]{};size_t count=0;
      ok(aii_voice_enrollment_finals(s,eligible,16,&count,&error));
      need(count==1 && eligible[0]==final_sequence,"recognized final lost enrollment custody");
    }
    const std::string recovery_text="The opening words were retained, and this is the complete recovery reply.";
    synth_started=Clock::now();ok(aii_voice_synthesize(s,2,recovery_text.data(),recovery_text.size(),&error));
    wait([&]{return ended[2] && !status().synthesizing;});recovery_seconds=std::chrono::duration<double>(Clock::now()-synth_started).count();ok(aii_voice_close(s,0,&error));
    need(aii_voice_wait(s,50,&error)==AII_VOICE_AGAIN,"drain guessed rendering");
    ok(aii_voice_playback(s,2,delivered[2],1,0,&error));ok(aii_voice_wait(s,2000,&error));pump();
    const auto v=status();need(v.retired && !v.aborted && v.recognized==input.size(),"owner/tail retirement");
    recovery.close();need(bool(recovery),"sink close");
    ok(aii_voice_release(&s,&error));ok(aii_voice_open(models,nullptr,&s,&error));
    ok(aii_voice_close(s,1,&error));ok(aii_voice_wait(s,2000,&error));ok(aii_voice_release(&s,&error));ok(aii_voice_models_release(&models,&error));
    std::cout<<"{\"kind\":\"complete\",\"tts_backend\":"<<quote(backend)<<",\"simulated_sink\":true,\"retired\":true,\"reopened\":true,\"final\":"<<quote(transcript)
      <<",\"models\":"<<(with_uid?5:4)<<",\"speaker_observations\":"<<observations
      <<",\"input_samples\":"<<input.size()<<",\"recovery_samples\":"<<delivered[2]<<",\"stop_admission_us\":"<<stop_us<<",\"cancel_admission_us\":"<<cancel_us
      <<",\"load_seconds\":"<<load_seconds<<",\"first_pcm_seconds\":"<<first_pcm_seconds
      <<",\"recovery_first_pcm_seconds\":"<<recovery_first_pcm_seconds<<",\"recovery_seconds\":"<<recovery_seconds<<"}\n";
    return 0; // Also called from the iOS app; not only C++'s special main.
  } catch(const std::exception& ex) {
    std::cerr<<"C ABI proof failed: "<<ex.what()<<'\n';
    if(s){aii_voice_close(s,1,&error);if(aii_voice_wait(s,2000,&error)==AII_VOICE_OK)aii_voice_release(&s,&error);}
    if(models)aii_voice_models_release(&models,&error);return 1;
  }
}
