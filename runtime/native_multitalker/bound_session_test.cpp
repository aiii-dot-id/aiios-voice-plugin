#include "bound_session.h"
#include <chrono>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

namespace fs=std::filesystem;
template<class F> void refused(F action,const char* reason) {
  try {action();}
  catch(const std::exception& error) {
    if(std::string(error.what()).find(reason)!=std::string::npos)return;
    throw std::runtime_error(std::string("wrong refusal: ")+error.what());
  }
  throw std::runtime_error("invalid model accepted");
}
int main(int argc,char** argv) {
  try {
    if(argc!=1 && argc!=2)throw std::invalid_argument("optional bound model root");
    const auto root=fs::temp_directory_path()/fs::u8path(
      "aii-bound-session-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count())+u8"-\u03a9-\u4e2d");
    if(!fs::create_directory(root))throw std::runtime_error("fresh fixture required");
    struct Cleanup {fs::path root;~Cleanup(){std::error_code ec;fs::remove_all(root,ec);}} cleanup{root};
    Ort::Env env{ORT_LOGGING_LEVEL_ERROR,"bound-session-test"};
    Ort::SessionOptions options;
    options.SetIntraOpNumThreads(2);options.SetInterOpNumThreads(1);
    options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_DISABLE_ALL);
    auto load=[&](const fs::path& path,const char* name){return aii::multitalker::bound_session(env,path.u8string(),name,options);};
    refused([&]{load(root,"../escape");},"unbound hearing graph");
    refused([&]{load(root,"unknown");},"unbound hearing graph");
    refused([&]{load(root,"asr_encoder");},"open bound model");
    fs::create_directory(root/"asr_encoder");
    const auto graph=root/"asr_encoder"/"model.onnx";
    {std::ofstream out(graph,std::ios::binary);out.put('x');}
    refused([&]{load(root,"asr_encoder");},"extent differs");
    {std::ofstream out(graph,std::ios::binary);out.seekp(2358301);out.put('x');}
    refused([&]{load(root,"asr_encoder");},"SHA256 differs");
    if(argc==2) {
      const fs::path models(argv[1]);
      fs::copy_file(models/"asr_encoder"/"model.onnx",graph,fs::copy_options::overwrite_existing);
      refused([&]{load(root,"asr_encoder");},"open bound model");
      {std::ofstream out(root/"asr_encoder"/"weights-000.bin",std::ios::binary);out.put('x');}
      refused([&]{load(root,"asr_encoder");},"extent differs");
      for(const auto* name:{"asr_decoder","asr_encoder","asr_joiner","asr_preencode","diar_preencode","diar_classifier"}) {
        auto session=load(models,name);
        if(!session.GetInputCount() || !session.GetOutputCount())throw std::runtime_error("empty graph signature");
      }
    }
    std::cout<<"bound-session: passed; real-models="<<(argc==2)<<'\n';
    return 0;
  }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
