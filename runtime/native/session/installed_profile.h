#pragma once
#include "worker_json.h"
#include "../platform/paths.h"
#include <array>
#include <filesystem>
#include <fstream>
namespace aii::voice::wire {
// This profile is part of the carrier-verified immutable runtime, not settings
// supplied by an identity. All models remain host-owned data under its root.
struct InstalledProfile {
  std::array<std::string,11> arguments;
  std::string asr_execution;
  std::string previous_uid_policy;
  static std::filesystem::path within(const std::filesystem::path& root,const std::string& name,bool directory) {
    namespace fs=std::filesystem;
    require(!name.empty()&&name.size()<=1024&&name.find_first_of("\\:")==std::string::npos,"invalid model-relative path");
    const auto relative=fs::u8path(name);
    require(!relative.is_absolute()&&relative.generic_u8string()==name,"model path must be relative");
    auto current=aii::platform::existing_io_path(root);
    require(fs::is_directory(current),"model root unavailable");
    for(const auto& part:relative) {
      require(part!="."&&part!=".."&&!part.empty(),"model path traversal");
      current/=part;
      require(!fs::is_symlink(fs::symlink_status(current)),"model path symlink refused");
    }
    require(directory?fs::is_directory(current):fs::is_regular_file(current),"model path has wrong kind or is missing");
    return current;
  }
  static InstalledProfile read(const std::filesystem::path& runtime,const std::filesystem::path& models) {
    namespace fs=std::filesystem;
    const auto file=runtime/"native-profile.json";
    require(!fs::is_symlink(fs::symlink_status(file))&&fs::is_regular_file(file),"bound native profile required");
    std::ifstream in(file,std::ios::binary);char bytes[32769];in.read(bytes,sizeof bytes);
    require(in.eof()&&in.gcount()>0&&in.gcount()<=32768,"native profile read/size bound");
    auto j=parse(std::string(bytes,size_t(in.gcount())));
    const auto* execution=field(j.get(),"asr_execution");
    const auto* previous=field(j.get(),"uid_previous_policy");
    require(cJSON_IsObject(j.get())&&cJSON_GetArraySize(j.get())==4+bool(execution)+bool(previous),"native profile fields differ");
    require(str(field(j.get(),"schema"))=="aiii.voice.native-profile","native profile schema differs");
    auto backend=str(field(j.get(),"backend"));require(backend=="cpu"||backend=="vulkan"||backend=="metal","native backend differs");
    const auto* paths=field(j.get(),"models");
    require(cJSON_IsObject(paths)&&cJSON_GetArraySize(paths)==8,"native model set differs");
    constexpr std::array<const char*,8> keys={"asr","asr_mel","vad","endpoint","endpoint_coefficients","tts","tts_config","uid"};
    InstalledProfile p;p.arguments[0]="installed-native-worker";p.arguments[8]=backend;
    if(execution) {
      require(cJSON_IsObject(execution),"ASR execution must be an object");
      p.asr_execution=encode(clone(execution));
      require(p.asr_execution.size()<=2048,"ASR execution profile bound");
    }
    for(size_t i=0;i<keys.size();++i)p.arguments[i<7?i+1:9]=within(models,str(field(paths,keys[i]),1024),i==0||i==5).u8string();
    p.arguments[10]=within(runtime,str(field(j.get(),"uid_policy"),1024),false).u8string();
    if(previous)p.previous_uid_policy=within(runtime,str(previous,1024),false).u8string();
    return p;
  }
};
}
