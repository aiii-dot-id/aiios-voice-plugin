// Retained proof runner only. Its fixture contains a typed projection prepared
// from the EXISTING canonical snapshot codec. This is not a shipping importer
// or a second enrollment format. No expected words enter the recognizer.
#include "native_models.h"
#include "native_speaker.h"
#include "worker_json.h"
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <thread>
using namespace aii::voice;
using namespace aii::voice::wire;
using Clock=std::chrono::steady_clock;
double seconds(Clock::time_point t){return std::chrono::duration<double>(Clock::now()-t).count();}
std::string read_bytes(const std::string& path,size_t max) {
  std::ifstream f(path,std::ios::binary|std::ios::ate);const auto n=f.tellg();
  require(f&&n>0&&uint64_t(n)<=max,"bounded proof input required");
  std::string s(size_t(n),'\0');f.seekg(0);f.read(s.data(),n);require(bool(f),"proof read failed");return s;
}
std::vector<float> pcm16(const std::string& path) {
  const auto raw=read_bytes(path,960000);require(raw.size()%2==0,"PCM bytes differ");
  std::vector<float> pcm(raw.size()/2);
  for(size_t i=0;i<pcm.size();++i){int n=uint8_t(raw[2*i])|(uint32_t(uint8_t(raw[2*i+1]))<<8);if(n>=32768)n-=65536;pcm[i]=float(n)/32768;}
  return pcm;
}
aii::uid::Vector vector(const cJSON* a) {
  require(cJSON_IsArray(a)&&cJSON_GetArraySize(a)==256,"fixture vector differs");
  aii::uid::Vector v{};for(size_t i=0;i<256;++i)v[i]=cJSON_GetArrayItem(a,int(i))->valuedouble;return v;
}
void report(const char* kind,const std::string& id,const std::string& raw) {
  auto j=object();put(j,"kind",string(kind));put(j,"id",string(id));put(j,"result",parse(raw));
  std::cout<<encode(j)<<'\n'<<std::flush;
}
std::string decision(const aii::uid::Decision& d) {
  auto j=object();put(j,"outcome",string(d.outcome));put(j,"reason",string(d.reason));
  put(j,"speaker_id",d.speaker_id.empty()?null():string(d.speaker_id));put(j,"label",d.label.empty()?null():string(d.label));
  put(j,"score",d.score?own(cJSON_CreateNumber(*d.score)):null());put(j,"margin",d.margin?own(cJSON_CreateNumber(*d.margin)):null());
  put(j,"enrollment_revision",string(std::to_string(d.enrollment_revision)));put(j,"policy_sha256",string(d.policy_sha256));return encode(j);
}
int main(int argc,char** argv){try {
  require(argc==3,"fixture and fresh output required");
  const auto raw=read_bytes(argv[1],4<<20);
  auto fixture=own(cJSON_ParseWithLength(raw.data(),raw.size())); // hash-bound proof fixture, not host input
  const auto* p=field(fixture.get(),"policy");
  aii::uid::Policy policy{str(field(p,"embedding_binding")),str(field(p,"calibration_sha256")),str(field(p,"fingerprint")),
                        field(p,"threshold")->valuedouble,field(p,"minimum_margin")->valuedouble,unsigned(integer(field(p,"minimum_enrollment_samples"),8))};
  aii::uid::Snapshot snapshot{policy,integer(field(fixture.get(),"revision")),{}};
  const auto* speakers=field(fixture.get(),"speakers");
  for(auto* s=speakers->child;s;s=s->next) {
    aii::uid::Speaker speaker{str(field(s,"id")),str(field(s,"label")),{}};
    for(auto* row=field(s,"samples")->child;row;row=row->next)
      speaker.samples.push_back({str(field(row,"audio_sha256")),vector(field(row,"embedding"))});
    snapshot.speakers.push_back(std::move(speaker));
  }
  aii::uid::validate(snapshot,policy);
  std::filesystem::path out(argv[2]);require(std::filesystem::create_directory(out),"fresh output required");
  bool unreadable=false;
  NativeSpeaker uid(str(field(fixture.get(),"uid_model"),4096),"cpu",policy,[&]{
    if(unreadable)throw std::runtime_error("injected unreadable host snapshot");return snapshot;
  });
  uid.open(); // explicit standalone evidence session, before the Session-owned one
  const auto begin=Clock::now();size_t count=0;
  for(auto* row=field(fixture.get(),"cases")->child;row;row=row->next) {
    const auto id=str(field(row,"id"));
    report("decision",id,decision(aii::uid::identify(snapshot,policy,vector(field(row,"embedding")),policy.embedding_binding)));
    report("native_embedding_decision",id,uid.identify(++count,pcm16(str(field(row,"pcm"),4096))));
  }
  std::cout<<"{\"kind\":\"panel_complete\",\"queries\":"<<count<<",\"seconds\":"<<seconds(begin)<<"}\n"<<std::flush;
  const auto query=pcm16(str(field(field(fixture.get(),"cases")->child,"pcm"),4096));
  unreadable=true;bool refused=false;try{uid.identify(++count,query);}catch(const std::runtime_error&){refused=true;}
  require(refused,"unreadable enrollment became empty");unreadable=false;
  const auto* paths=field(fixture.get(),"model_paths");
  auto path=[&](int i){return str(cJSON_GetArrayItem(paths,i),4096);};
  NativeModels models({path(0),path(1),path(2),path(3),path(4),path(5),path(6)});
  auto input=pcm16(str(field(fixture.get(),"conversation_pcm"),4096));input.resize(input.size()+32000,0);
  Session s(models.recognizer(),models.vad(),models.endpoint(),models.synthesizer(),{},&uid);
  const auto started=Clock::now();bool first=false,interrupted=false;uint64_t delivered[3]={};bool ended[3]={};
  std::ofstream a(out/"interrupted.f32",std::ios::binary),b(out/"recovery.f32",std::ios::binary);
  size_t finals=0,observations=0;std::vector<Event> observed;
  auto pump=[&]{
    require(seconds(started)<120,"conversation deadline");require(s.status().error.empty(),s.status().error.c_str());
    Event e;while(s.event(e)) {
      interrupted|=e.kind=="interruption_requested";finals+=e.kind=="transcript_final";observations+=e.kind=="speaker_observation";
      if(e.kind=="speaker_observation")observed.push_back(e);
      auto j=object();put(j,"kind",string(e.kind));put(j,"sequence",number(e.sequence));put(j,"refers_to",number(e.refers_to));
      put(j,"start",number(e.start));put(j,"end",number(e.end));put(j,"text",string(e.text));
      put(j,"seconds",own(cJSON_CreateNumber(seconds(started))));std::cout<<encode(j)<<'\n'<<std::flush;
    }
    Audio audio;while(s.audio(audio)) {
      require(audio.generation>=1&&audio.generation<=2&&audio.start==delivered[audio.generation],"audio identity/clock differs");
      if(audio.end)ended[audio.generation]=true;
      else {
        require(!(interrupted&&audio.generation==1),"stale interrupted audio");first=true;
        auto& f=audio.generation==1?a:b;f.write(reinterpret_cast<const char*>(audio.pcm.data()),audio.pcm.size()*4);
        require(bool(f),"sink failed");delivered[audio.generation]+=audio.pcm.size();
      }
    }
  };
  auto wait=[&](auto predicate){while(!predicate()){pump();std::this_thread::sleep_for(std::chrono::milliseconds(1));}pump();};
  s.synthesize(1,"The local voice platform is speaking now. Please interrupt this sentence while it continues to speak.");
  wait([&]{return first;});const auto input_started=Clock::now();
  for(size_t offset=0;offset<input.size();) {
    const auto n=std::min<size_t>(997,input.size()-offset);
    if(s.feed(offset,input.data()+offset,n))offset+=n;
    pump();while(seconds(input_started)<double(offset)/16000){pump();std::this_thread::sleep_for(std::chrono::milliseconds(1));}
  }
  s.finish_input(input.size());wait([&]{return s.status().input_finished&&!s.status().synthesizing&&ended[1];});
  require(interrupted&&finals&&delivered[1],"real interruption/final missing");s.playback(1,delivered[1],true,true);
  s.synthesize(2,"The opening words were retained, and this is the complete recovery reply.");
  wait([&]{return !s.status().synthesizing&&ended[2];});s.close(false);
  require(!s.wait_closed(30),"drain did not wait for render receipt");s.playback(2,delivered[2],true,false);
  wait([&]{return s.wait_closed(1);});require(finals==observations,"a final has no speaker decision");
  for(const auto& e:observed) {
    require(e.refers_to&&e.refers_to<e.sequence&&e.start<e.end&&e.end<=input.size(),"speaker final/span differs");
    auto observation=parse(e.text);
    if(str(field(observation.get(),"outcome"))!="unavailable") {
      const std::vector<float> span(input.begin()+e.start,input.begin()+e.end);
      const auto repeat=uid.identify(++count,span);
      require(repeat==e.text,"concurrent UID differs from the exact isolated final span");
    }
  }
  std::cout<<"{\"kind\":\"complete\",\"finals\":"<<finals<<",\"speaker_observations\":"<<observations
           <<",\"recovery_samples\":"<<delivered[2]<<",\"retired\":true,\"simulated_sink\":true}\n";
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
