#include "c_api.h"
#include <cstring>
#include <iostream>
#include <stdexcept>
int main() {
  try {
    aii_voice_paths paths{"absent","absent","absent","absent","absent","absent","absent"};
    const char* refused[]={nullptr,"","auto","cuda","Vulkan","cpu ","Metal","metal "};
    for(const char* backend:refused) {
      aii_voice_models* out=nullptr;aii_voice_error error{};
      const auto rc=aii_voice_models_load_with_backend(&paths,backend,&out,&error);
      if(rc!=AII_VOICE_INVALID || out || !std::strstr(error.message,"no fallback"))
        throw std::runtime_error("unknown backend was not refused before model loading");
    }
    // A recognized Metal spelling reaches path validation, without loading
    // a model or substituting a CPU backend. Device admission is the real gate.
    paths.asr="";
    aii_voice_models* out=nullptr;aii_voice_error error{};
    const auto rc=aii_voice_models_load_with_backend(&paths,"metal",&out,&error);
    if(rc!=AII_VOICE_INVALID || out || !std::strstr(error.message,"bound ASR path required"))
      throw std::runtime_error("explicit Metal spelling was not recognized before path validation");
    // Missing ASR now has a specific diagnosis because injected recognizers
    // may omit it. Other bound paths must still be checked independently.
    paths.asr="absent";paths.mel="";
    const auto other=aii_voice_models_load_with_backend(&paths,"metal",&out,&error);
    if(other!=AII_VOICE_INVALID || out || !std::strstr(error.message,"every bound model path required"))
      throw std::runtime_error("non-ASR model path validation was bypassed");
    paths.mel="absent";
    for(const auto n:{size_t(0),size_t(4097)}) {
      const auto invalid=aii_voice_models_load_uid_policies(&paths,"cpu","uid","{}",2,nullptr,nullptr,
          nullptr,"{}",n,&out,&error);
      if(invalid!=AII_VOICE_INVALID||out||!std::strstr(error.message,"bounded previous UID policy required"))
        throw std::runtime_error("partial or oversized prior policy reached model loading");
    }
    std::cout<<"explicit backend refusal before load; no silent fallback\n";
  } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
