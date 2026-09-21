// Development transport for testing the production codec/decision functions
// with real native embeddings. Not included in the distributed runtime.
#include "speaker_registry.h"
#include "../native/session/worker_json.h"
#include <iostream>
using namespace aii::uid;
using namespace aii::voice::wire;
int main(){try{
  std::string raw;char c;
  while(std::cin.get(c)){if(raw.size()>12u<<20)throw std::runtime_error("probe input bound");raw+=c;}
  auto input=parse(raw);auto p=read_policy(str(field(input.get(),"policy"),4096));
  const auto* base=field(input.get(),"document");
  const auto current=cJSON_IsNull(base)?write_registry({0,{p.policy,0,{}},{}},p):str(base,8u<<20);
  const auto expected=integer(field(input.get(),"expected_revision"));
  const auto id=str(field(input.get(),"uuid"),36),op=str(field(input.get(),"operation"));RegistryChange result;
  if(op=="observe") {
    const auto* s=field(input.get(),"sample");std::optional<Sample> sample;
    if(!cJSON_IsNull(s))sample=Sample{str(field(s,"audio_sha256"),64),decode_vector(str(field(s,"embedding_f64le_b64"),2732))};
    result=observe_speaker(current,p,expected,id,sample);
  }else if(op=="associate") {
    result=associate_speaker(current,p,expected,id,str(field(input.get(),"label"),512),"");
  }else throw std::runtime_error("unknown probe operation");
  auto output=object();put(output,"document",string(result.document));put(output,"uuid",string(result.uuid));
  put(output,"revision",number(result.revision));put(output,"continuity",string(result.continuity));put(output,"reason",string(result.reason));
  std::cout<<encode(output)<<'\n';return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
