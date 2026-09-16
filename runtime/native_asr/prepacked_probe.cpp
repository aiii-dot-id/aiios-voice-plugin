// Private exact-runtime export/reload diagnostic. No production loading switch.
#include "initializers.h"
#include <chrono>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>

namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;
static double elapsed(Clock::time_point t) {
  return std::chrono::duration<double>(Clock::now()-t).count();
}
static Ort::SessionOptions options() {
  Ort::SessionOptions o;
  o.SetIntraOpNumThreads(4); o.SetInterOpNumThreads(1);
  o.SetExecutionMode(ORT_SEQUENTIAL);
  o.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_ALL);
  return o;
}
int main(int argc,char** argv) {
  try {
    if(argc!=5) throw std::runtime_error("export ROOT OUT PACKED or reload GRAPH ROOT PACKED");
    const std::string mode=argv[1], packed=argv[4];
    if((packed!="0"&&packed!="1")||(mode!="export"&&mode!="reload"))
      throw std::runtime_error("explicit diagnostic mode required");
    Ort::Env env(ORT_LOGGING_LEVEL_WARNING,"aii-native-prepacked-diagnostic");
    env.DisableTelemetryEvents();
    std::cout<<std::setprecision(17);
    const auto start=Clock::now();
    auto o=options();
    if(mode=="export") {
      const fs::path root=fs::u8path(argv[2]), out=fs::u8path(argv[3]);
      if(!fs::is_directory(out)||!fs::is_empty(out)) throw std::runtime_error("fresh empty export directory required");
      auto weights=std::make_unique<aii::asr::Initializers>(root);
      const auto mapped=elapsed(start);
      weights->attach(o);
      const auto graph=out/"encoder.onnx";
      o.SetOptimizedModelFilePath(graph.c_str());
      o.AddConfigEntry("session.optimized_model_external_initializers_file_name","weights.bin");
      o.AddConfigEntry("session.optimized_model_external_initializers_min_size_in_bytes","1024");
      o.AddConfigEntry("session.save_external_prepacked_constant_initializers",packed.c_str());
      const auto build=Clock::now();
      {
        Ort::Session session(env,weights->encoder.data(),weights->encoder.size(),o);
        if(session.GetInputCount()!=7||session.GetOutputCount()!=5) throw std::runtime_error("encoder signature changed");
        weights->check_unchanged();
        std::cout<<"{\"kind\":\"export\",\"packed\":"<<packed
                 <<",\"mapping_seconds\":"<<mapped<<",\"construction_and_save_seconds\":"<<elapsed(build)
                 <<",\"inputs\":7,\"outputs\":5,\"runtime\":\""<<Ort::GetVersionString()<<"\"}\n"<<std::flush;
      }
    } else {
      const fs::path graph=fs::u8path(argv[2]), root=fs::u8path(argv[3]);
      if(graph.parent_path()!=root||graph.filename()!="encoder.onnx") throw std::runtime_error("explicit export root required");
      // The supervisor rehashes every derived graph/data file before and after
      // this private load. This is NOT the signed production loader contract.
      const auto load=Clock::now();
      {
        Ort::Session session(env,graph.c_str(),o);
        if(session.GetInputCount()!=7||session.GetOutputCount()!=5) throw std::runtime_error("encoder signature changed");
        std::cout<<"{\"kind\":\"reload\",\"packed\":"<<packed
                 <<",\"seconds\":"<<elapsed(load)<<",\"inputs\":7,\"outputs\":5,\"runtime\":\""
                 <<Ort::GetVersionString()<<"\"}\n"<<std::flush;
      }
    }
    std::cout<<"{\"kind\":\"retired\",\"seconds\":"<<elapsed(start)<<"}\n"<<std::flush;
    return 0;
  } catch(const std::exception& e) {
    std::cerr<<"prepacked diagnostic refused: "<<e.what()<<'\n';return 1;
  }
}
