// Build-time cache generator/probe, never a distributed runtime executable.
#include "../native/platform/coreml_candidate.h"
#include <cmath>
#include <fstream>
#include <iostream>
#include <iterator>
#include <vector>

int main(int argc,char** argv) {
  try {
    if(argc!=4 || (std::string(argv[1])!="seed" && std::string(argv[1])!="verify"))
      throw std::invalid_argument("expected seed|verify model.onnx cache-directory");
    const bool seed=std::string(argv[1])=="seed";
    if(seed) {
      if(std::filesystem::exists(argv[3])) throw std::invalid_argument("cache already exists");
      std::filesystem::create_directories(argv[3]);
    }
    std::ifstream file(argv[2],std::ios::binary|std::ios::ate);
    if(!file || file.tellg()<=0 || file.tellg()>512*1024*1024)
      throw std::invalid_argument("model is absent or exceeds build-tool bound");
    file.seekg(0);
    std::vector<char> bytes((std::istreambuf_iterator<char>(file)),{});
    Ort::Env env(ORT_LOGGING_LEVEL_WARNING,"uid-cache-build");
    Ort::SessionOptions options;
    options.SetIntraOpNumThreads(2);options.SetInterOpNumThreads(1);
    options.SetExecutionMode(ORT_SEQUENTIAL);
    aii::platform::coreml_provider(options,"NeuralNetwork",argv[3]);
    Ort::Session session(env,bytes.data(),bytes.size(),options);
    Ort::AllocatorWithDefaultOptions allocator;
    const auto in=session.GetInputNameAllocated(0,allocator);
    const auto out=session.GetOutputNameAllocated(0,allocator);
    const char* inputs[]={in.get()};const char* outputs[]={out.get()};
    for(const int64_t frames:{200,400}) {
      std::vector<float> features(static_cast<size_t>(frames)*80);
      for(size_t i=0;i<features.size();++i) features[i]=std::sin(float(i)*0.021f);
      const int64_t shape[]={1,frames,80};
      auto memory=Ort::MemoryInfo::CreateCpu(OrtArenaAllocator,OrtMemTypeDefault);
      auto tensor=Ort::Value::CreateTensor<float>(memory,features.data(),features.size(),shape,3);
      auto result=session.Run(Ort::RunOptions{nullptr},inputs,&tensor,1,outputs,1);
      if(result[0].GetTensorTypeAndShapeInfo().GetElementCount()!=192)
        throw std::runtime_error("ECAPA output dimensions differ");
      for(size_t i=0;i<192;++i) if(!std::isfinite(result[0].GetTensorData<float>()[i]))
        throw std::runtime_error("nonfinite cache inference");
    }
    std::cout<<"cache synthetic inference passed; not recorded speaker qualification\n";
    return 0;
  } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
