#pragma once
#include "installed_profile.h"
#include <cmath>

namespace aii::voice::wire {
// Sealed native profile, never caller settings. Empty preserves the older
// recognizer. A declared Nemotron profile must load its bound data and device;
// unavailable backends must fail, not silently fall back.
struct HearingProfile {
  std::string model;
  std::string separator_backend,separator_model;
  int separator_threads=2,separator_cuda=-1;
  int gpu=-1,encoder_cuda=-1,encoder_threads=2;
  static HearingProfile read(const std::string& raw,const std::filesystem::path& graphs) {
    HearingProfile result;
    if(raw.empty())return result;
    auto j=parse(raw);
    const auto count=cJSON_GetArraySize(j.get());
    const auto* threads=field(j.get(),"encoder_threads");
    const auto* separator=field(j.get(),"separator");
    require(cJSON_IsObject(j.get())&&count==3+bool(threads)+bool(separator),"hearing execution fields differ");
    require(str(field(j.get(),"diarizer"))=="nemotron","unsupported hearing diarizer");
    auto device=[&](const char* name,int minimum) {
      const auto* value=field(j.get(),name);
      require(cJSON_IsNumber(value)&&std::isfinite(value->valuedouble)&&
        value->valuedouble>=minimum&&value->valuedouble<=63&&
        value->valuedouble==std::floor(value->valuedouble),"invalid hearing device");
      return static_cast<int>(value->valuedouble);
    };
    // The native diarizer ABI uses -1 for explicit CPU execution. This does
    // not remove diarization or turn an unavailable GPU into a fallback.
    result.gpu=device("gpu",-1);result.encoder_cuda=device("encoder_cuda",-1);
    if(threads) {
      require(cJSON_IsNumber(threads)&&std::isfinite(threads->valuedouble)&&
        threads->valuedouble>=1&&threads->valuedouble<=16&&
        threads->valuedouble==std::floor(threads->valuedouble),"invalid hearing thread count");
      result.encoder_threads=static_cast<int>(threads->valuedouble);
    }
    result.model=InstalledProfile::within(graphs,"nemotron.gguf",false).u8string();
    if(separator) {
      require(cJSON_IsObject(separator),"separator execution must be an object");
      result.separator_backend=str(field(separator,"backend"));
      if(result.separator_backend=="coreml") {
        require(cJSON_GetArraySize(separator)==1,"Core ML separator fields differ");
        result.separator_model=InstalledProfile::within(graphs,"separator-coreml",true).u8string();
      } else {
        require(result.separator_backend=="onnx"&&cJSON_GetArraySize(separator)==3,"ONNX separator fields differ");
        auto integer=[&](const char* key,int low,int high) {
          const auto* value=field(separator,key);
          require(cJSON_IsNumber(value)&&std::isfinite(value->valuedouble)&&value->valuedouble>=low&&
              value->valuedouble<=high&&value->valuedouble==std::floor(value->valuedouble),"invalid separator execution");
          return static_cast<int>(value->valuedouble);
        };
        result.separator_threads=integer("threads",1,64);result.separator_cuda=integer("cuda",-1,63);
        result.separator_model=InstalledProfile::within(graphs,"separator.onnx",false).u8string();
      }
    }
    return result;
  }
};
}
