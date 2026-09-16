// Bounded public-API mechanism proof. Borrowed storage outlives the session.
#include "onnxruntime_cxx_api.h"
#include <array>
#include <fstream>
#include <iostream>
#include <iterator>
#include <vector>

int main(int argc, char** argv) {
  try {
    if (argc != 2) return 2;
    std::ifstream file(argv[1], std::ios::binary);
    std::vector<char> bytes{std::istreambuf_iterator<char>(file), {}};
    if (bytes.empty()) return 3;
    std::array<float,32> weights;
    for (size_t i=0; i<weights.size(); ++i) weights[i]=float(i);
    const std::array<int64_t,1> dims{32};
    Ort::Env env{ORT_LOGGING_LEVEL_ERROR,"editor-lifetime"};
    env.DisableTelemetryEvents();
    for (auto level : {ORT_DISABLE_ALL, ORT_ENABLE_ALL}) {
      Ort::SessionOptions opts;
      opts.SetIntraOpNumThreads(1); opts.SetInterOpNumThreads(1);
      opts.SetGraphOptimizationLevel(level);
      auto session=Ort::Session::CreateModelEditorSession(env,bytes.data(),bytes.size(),opts);
      std::cout << "{\"stage\":\"editor_created\",\"level\":" << int(level) << "}" << std::endl;
      {
        auto info=Ort::MemoryInfo::CreateCpu(OrtDeviceAllocator,OrtMemTypeDefault);
        auto value=Ort::Value::CreateTensor<float>(info,weights.data(),weights.size(),dims.data(),dims.size());
        Ort::Graph graph;
        graph.AddInitializer("weight",value,true);
        // The base graph declares the not-yet-bound weight as an input. Once
        // bound, remove it from the callable signature, retaining the real
        // input's exact type rather than inventing a replacement shape.
        auto type=session.GetInputTypeInfo(0);
        std::vector<Ort::ValueInfo> inputs;
        inputs.emplace_back("input",type.GetConst());
        graph.SetInputs(inputs);
        Ort::Model update({{"",18}});
        update.AddGraph(graph);
        session.FinalizeModelEditorSession(update,opts,nullptr);
      }
      if (session.GetInputCount()!=1) return 5;
      std::array<float,32> data{};
      auto info=Ort::MemoryInfo::CreateCpu(OrtDeviceAllocator,OrtMemTypeDefault);
      auto input=Ort::Value::CreateTensor<float>(info,data.data(),data.size(),dims.data(),dims.size());
      const char *in="input", *out="output";
      auto result=session.Run(Ort::RunOptions{nullptr},&in,&input,1,&out,1);
      const float* got=result[0].GetTensorData<float>();
      for (size_t i=0;i<weights.size();++i) if(got[i]!=weights[i]) return 4;
      std::cout << "{\"stage\":\"inference\",\"level\":" << int(level) << ",\"passed\":true}" << std::endl;
    }
    return 0;
  } catch (const std::exception& e) {
    std::cerr << e.what() << std::endl;
    return 1;
  }
}
