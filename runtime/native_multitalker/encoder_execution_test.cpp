#include "onnx_backend.h"
#include <onnxruntime_cxx_api.h>
#include <algorithm>
#include <iostream>
#include <stdexcept>

int main() {try {
  using aii::multitalker::EncoderExecution;
  for(const auto& config: {EncoderExecution{0,-1,{}}, EncoderExecution{17,-1,{}},
                         EncoderExecution{2,-2,{}}, EncoderExecution{2,128,{}}}) {
    bool refused=false;
    try {aii::multitalker::OnnxEncoder encoder("missing-fixture-graphs",config);}
    catch(const std::invalid_argument& e) {
      refused=std::string(e.what())=="encoder execution configuration";
    }
    if(!refused)throw std::runtime_error("invalid execution configuration accepted");
  }
  const auto providers=Ort::GetAvailableProviders();
  if(std::find(providers.begin(),providers.end(),"CUDAExecutionProvider")==providers.end()) {
    bool refused=false;
    try {aii::multitalker::OnnxEncoder encoder("missing-fixture-graphs",{2,0,{}});}
    catch(const std::runtime_error& e) {
      refused=std::string(e.what())=="requested encoder CUDA provider is unavailable";
    }
    if(!refused)throw std::runtime_error("unavailable CUDA selection fell through to CPU");
  }
  return 0;
}catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}}
