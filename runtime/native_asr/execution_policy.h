#pragma once
#include "../native/session/worker_json.h"
#include <algorithm>
#include <vector>

namespace aii::asr {
// Immutable model-initialization policy, not a session control or environment
// override. Windows indices refer to EnumAdapters1, not preference ordering.
struct ExecutionPolicy {
  bool directml=false;
  int adapter=-1; // -1 means Windows high-performance preference
  bool target_default=true;
  static ExecutionPolicy read(const char* raw) {
    using namespace aii::voice::wire;
    if(!raw)return {};
    size_t n=0;while(n<=2048&&raw[n])++n;
    require(n&&n<=2048,"bounded ASR execution JSON required");
    auto j=parse(std::string(raw,n));
    require(cJSON_IsObject(j.get()),"ASR execution object required");
    const auto provider=str(field(j.get(),"provider"));
    if(provider=="cpu") {
      require(cJSON_GetArraySize(j.get())==1,"CPU ASR accepts only provider");
      return {false,-1,false};
    }
    require(provider=="directml"&&cJSON_GetArraySize(j.get())==2,
            "explicit cpu or directml ASR provider required");
    const auto* adapter=field(j.get(),"adapter");
    if(cJSON_IsString(adapter)) {
      require(str(adapter)=="high_performance","unknown ASR adapter preference");
      return {true,-1,false};
    }
    return {true,int(integer(adapter,127)),false};
  }
};
struct AdapterCandidate {
  unsigned index=0, preference=0;
  bool software=false, d3d12=false;
};
inline unsigned select_adapter(const ExecutionPolicy& p,const std::vector<AdapterCandidate>& devices) {
  if(!p.directml)throw std::invalid_argument("GPU selection requires DirectML policy");
  const AdapterCandidate* best=nullptr;
  for(const auto& device:devices) {
    if(p.adapter>=0 && device.index!=unsigned(p.adapter))continue;
    if(device.software||!device.d3d12)continue;
    if(!best||device.preference<best->preference)best=&device;
  }
  if(!best)throw std::runtime_error("requested ASR hardware adapter unavailable; no CPU substitution");
  return best->index;
}
}
