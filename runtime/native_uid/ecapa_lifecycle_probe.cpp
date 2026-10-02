// Development-only real-model NativeSpeaker/registry probe. Never packaged.
#include "../native/session/native_speaker.h"
#include "../native/session/worker_json.h"
#include "speaker_registry.h"
#include <fstream>
#include <iostream>
using namespace aii::uid;
using namespace aii::voice::wire;
std::string read(const std::string& path,size_t bound){
  std::ifstream f(path,std::ios::binary|std::ios::ate);require(bool(f),"proof input unavailable");
  const auto n=f.tellg();require(n>0&&uint64_t(n)<=bound,"proof input bound");
  std::string value(size_t(n),'\0');f.seekg(0);f.read(value.data(),n);require(bool(f),"proof short read");return value;
}
std::vector<float> pcm(const std::string& path){
  const auto bytes=read(path,960000);require(bytes.size()%2==0,"PCM length");
  std::vector<float> out(bytes.size()/2);
  for(size_t i=0;i<out.size();++i){int x=uint8_t(bytes[2*i])|(int(uint8_t(bytes[2*i+1]))<<8);if(x>=32768)x-=65536;out[i]=float(x)/32768;}
  return out;
}
int main(int argc,char** argv){try{
  require(argc==2,"one private fixture required");auto input=parse(read(argv[1],12u<<20));
  const auto policy=read_policy(str(field(input.get(),"policy"),4096));
  require(policy.policy.embedding_binding==ecapa_binding,"ECAPA binding required");
  const auto* base=field(input.get(),"document");
  auto document=cJSON_IsNull(base)?std::string{}:str(base,8u<<20);
  if(document.empty())document=write_registry({0,{policy.policy,0,{}},{}},policy);
  const auto id=str(field(input.get(),"uuid"),36);
  const auto* selected=cJSON_GetObjectItemCaseSensitive(input.get(),"backend");
  const auto backend=selected?str(selected,32):std::string("cpu");
  const auto operation=str(field(input.get(),"operation"));
  require(operation=="init"||operation=="query","unknown lifecycle proof operation");
  aii::voice::NativeSpeaker engine(str(field(input.get(),"model"),4096),backend,policy.policy,[&]{return read_registry(document,policy).profiles;});
  engine.warm();
  if(operation=="init"){
    const auto a=engine.prepare_capture(pcm(str(field(input.get(),"first"),4096)));
    const auto b=engine.prepare_capture(pcm(str(field(input.get(),"second"),4096)));
    auto prepared=observe_speaker(document,policy,0,id,a,ProfileAdmission::Corroborated,b);
    require(prepared.uuid==id,"first profile not admitted");
    document=associate_speaker(prepared.document,policy,prepared.revision,id,"Speaker One","colleague").document;
  }else{
    const auto original=document;const auto audio=pcm(str(field(input.get(),"audio"),4096));
    const bool expect_known=flag(field(input.get(),"expect_known"));
    size_t observed=0;
    engine.track_observer([&](const std::optional<Sample>& sample,size_t){
      require(sample.has_value()&&sample->embedding.size()==192,"actual ECAPA evidence missing");
      auto found=observe_speaker(document,policy,read_registry(document,policy).revision,
          "00000000-0000-4000-8000-000000000099",sample);
      require(expect_known?found.uuid==id:found.uuid.empty(),"native speaker continuity/control mismatch");
      require(found.document==original,"recognition silently changed registry");++observed;
      return std::string("{}");
    });
    for(int session=0;session<3;++session){engine.open();engine.identify_track(1,audio);engine.cancel();}
    require(observed==3,"session count differs");
  }
  auto result=object();put(result,"document",string(document));put(result,"completed",boolean(true));
  std::cout<<encode(result)<<'\n';return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
