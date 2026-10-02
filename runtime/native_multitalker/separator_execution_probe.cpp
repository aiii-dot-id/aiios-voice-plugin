// Development-only native execution proof. Not packaged or selected at runtime.
#include "onnxruntime_cxx_api.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <exception>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace {
using Clock=std::chrono::steady_clock;
void check(bool ok,const char* why){if(!ok)throw std::runtime_error(why);}
std::vector<float> read(const char* path,size_t maximum) {
  std::ifstream f(std::filesystem::u8path(path),std::ios::binary|std::ios::ate);
  const auto n=f.tellg();check(f&&n>0&&n%4==0&&uint64_t(n)<=maximum*4,"fixture extent");
  std::vector<float> out(size_t(n)/4);f.seekg(0);
  check(bool(f.read(reinterpret_cast<char*>(out.data()),n)),"fixture read");
  for(float x:out)check(std::isfinite(x),"nonfinite fixture");
  return out;
}
void provider(Ort::SessionOptions& options,const std::string& name,int threads) {
  options.SetIntraOpNumThreads(threads);options.SetInterOpNumThreads(1);
  if(name=="cpu")return;
  const auto available=Ort::GetAvailableProviders();
  if(name=="cuda") {
    check(std::find(available.begin(),available.end(),"CUDAExecutionProvider")!=available.end(),"CUDA provider absent");
    const auto& api=Ort::GetApi();OrtCUDAProviderOptionsV2* raw=nullptr;
    Ort::ThrowOnError(api.CreateCUDAProviderOptions(&raw));
    auto release=[&](OrtCUDAProviderOptionsV2* p){api.ReleaseCUDAProviderOptions(p);};
    std::unique_ptr<OrtCUDAProviderOptionsV2,decltype(release)> owner(raw,release);
    const char* keys[]={"use_tf32","gpu_mem_limit","cudnn_conv_algo_search","cudnn_conv_use_max_workspace"};
    const char* values[]={"0","2147483648","HEURISTIC","0"};
    Ort::ThrowOnError(api.UpdateCUDAProviderOptions(raw,keys,values,4));
    Ort::ThrowOnError(api.SessionOptionsAppendExecutionProvider_CUDA_V2(options,raw));return;
  }
  if(name=="coreml-gpu"||name=="coreml-ane") {
    check(std::find(available.begin(),available.end(),"CoreMLExecutionProvider")!=available.end(),"Core ML provider absent");
    // Explicit export shape operations are part of this diagnostic contract.
    // Optimizer/provider combinations require separate parity/resource proof.
    options.SetGraphOptimizationLevel(ORT_DISABLE_ALL);
    options.AppendExecutionProvider("CoreML",std::unordered_map<std::string,std::string>{
      {"MLComputeUnits",name=="coreml-gpu"?"CPUAndGPU":"CPUAndNeuralEngine"},
      {"ModelFormat","MLProgram"},{"RequireStaticInputShapes","0"},{"EnableOnSubgraphs","0"}});
    return;
  }
  throw std::runtime_error("unknown execution provider");
}
void cancel_inference(Ort::Session& session,Ort::Value& input,size_t test_case) {
  Ort::RunOptions run;
  const char* inputs[]={"pcm"};const char* outputs[]={"sources"};
  std::exception_ptr control_error;
  Clock::time_point requested{},admitted{};
  const auto started=Clock::now();
  // Baseline inference has already warmed this exact shape. The profile must
  // independently show kernel execution before termination, not just a refused
  // pre-cancelled Run. Allow the warmed call's host preparation on a VM before
  // requesting termination; admission and retirement bounds stay unchanged.
  // This thread is a test control, not a production timer.
  std::thread control([&] {
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    requested=Clock::now();
    try {run.SetTerminate();}catch(...) {control_error=std::current_exception();}
    admitted=Clock::now();
  });
  bool interrupted=false;
  std::exception_ptr inference_error;
  try {session.Run(run,inputs,&input,1,outputs,1);}
  catch(const Ort::Exception& error) {
    interrupted=error.GetOrtErrorCode()==ORT_FAIL&&
        std::string(error.what()).find("terminate")!=std::string::npos;
    if(!interrupted)inference_error=std::current_exception();
  }catch(...) {inference_error=std::current_exception();}
  const auto retired=Clock::now();
  control.join(); // No callback can retain RunOptions beyond this scope.
  if(control_error)std::rethrow_exception(control_error);
  if(inference_error)std::rethrow_exception(inference_error);
  const auto admission_ms=std::chrono::duration<double,std::milli>(admitted-requested).count();
  const auto retirement_ms=std::chrono::duration<double,std::milli>(retired-requested).count();
  const auto request_ms=std::chrono::duration<double,std::milli>(requested-started).count();
  std::cout<<"{\"case\":"<<test_case<<",\"cancelled\":"<<(interrupted?"true":"false")
    <<",\"cancel_requested_after_ms\":"<<request_ms
    <<",\"cancel_admission_ms\":"<<admission_ms<<",\"cancel_retirement_ms\":"<<retirement_ms<<"}"<<std::endl;
  check(interrupted&&retired>=requested,"cancellation did not interrupt inference");
  check(admission_ms<=50&&retirement_ms<=250,"cancellation exceeded bounded control/retirement gate");
}
}
int main(int argc,char** argv){try {
  int end=4,threads=4;bool cancellation=false,threads_set=false;
  while(end<argc&&std::string(argv[end]).rfind("--",0)!=0)++end;
  check(end>=6&&end%2==0,"model, provider, profile prefix, then input/reference pairs; optional --cancel and --threads N");
  for(int arg=end;arg<argc;++arg) {
    const std::string option=argv[arg];
    if(option=="--cancel") {check(!cancellation,"duplicate --cancel");cancellation=true;}
    else if(option=="--threads") {
      check(!threads_set&&arg+1<argc,"invalid --threads");
      const std::string value=argv[++arg];size_t consumed=0;
      threads=std::stoi(value,&consumed);
      check(consumed==value.size()&&threads>=1&&threads<=64,"threads must be 1..64");threads_set=true;
    }else throw std::runtime_error("unknown option");
  }
  const auto started=Clock::now();
  Ort::Env env(ORT_LOGGING_LEVEL_WARNING,"separator-proof");env.DisableTelemetryEvents();
  Ort::SessionOptions options;provider(options,argv[2],threads);
  options.EnableProfiling(std::filesystem::u8path(argv[3]).c_str());
  Ort::Session session(env,std::filesystem::u8path(argv[1]).c_str(),options);
  check(session.GetInputCount()==1&&session.GetOutputCount()==1,"separator arity");
  Ort::AllocatorWithDefaultOptions allocator;
  check(std::string(session.GetInputNameAllocated(0,allocator).get())=="pcm"&&
    std::string(session.GetOutputNameAllocated(0,allocator).get())=="sources","separator names");
  const auto startup=std::chrono::duration<double>(Clock::now()-started).count();
  for(int arg=4;arg<end;arg+=2) {
    auto pcm=read(argv[arg],480000);const auto expected=read(argv[arg+1],960000);
    check(pcm.size()>=32000&&expected.size()==2*pcm.size(),"separator fixture shape");
    for(float x:pcm)check(std::abs(x)<=1,"fixture PCM range");
    const int64_t shape[]={1,static_cast<int64_t>(pcm.size())};
    auto memory=Ort::MemoryInfo::CreateCpu(OrtArenaAllocator,OrtMemTypeDefault);
    auto input=Ort::Value::CreateTensor<float>(memory,pcm.data(),pcm.size(),shape,2);
    const char* inputs[]={"pcm"};const char* outputs[]={"sources"};
    for(int repeat=0;repeat<2;++repeat) {
      // First run is the baseline; the second must recover after cancellation
      // in the same session, with new per-call RunOptions and identical output.
      if(cancellation&&repeat==1)cancel_inference(session,input,static_cast<size_t>((arg-4)/2));
      const auto begin=Clock::now();
      auto result=session.Run(Ort::RunOptions{},inputs,&input,1,outputs,1);
      const auto elapsed=std::chrono::duration<double>(Clock::now()-begin).count();
      const auto info=result[0].GetTensorTypeAndShapeInfo();
      check(info.GetElementType()==ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT&&
        info.GetShape()==std::vector<int64_t>{1,2,shape[1]},"separator output shape");
      const auto* actual=result[0].GetTensorData<float>();double error=0,norm=0;
      for(size_t i=0;i<expected.size();++i) {
        check(std::isfinite(actual[i]),"nonfinite separated PCM");
        const double d=double(actual[i])-expected[i];error+=d*d;norm+=double(expected[i])*expected[i];
      }
      const auto relative=std::sqrt(error/std::max(norm,1e-24));
      std::cout<<"{\"case\":"<<(arg-4)/2<<",\"repeat\":"<<repeat
        <<",\"samples\":"<<pcm.size()<<",\"startup_seconds\":"<<startup
        <<",\"intra_op_threads\":"<<threads
        <<",\"runtime_version\":\""<<Ort::GetVersionString()<<"\""
        <<",\"inference_seconds\":"<<elapsed<<",\"relative_l2\":"<<relative<<"}"<<std::endl;
      check(relative<=2e-4,"native separator differs from untouched upstream reference");
    }
  }
  // The profile must be inspected for actual node placement; selecting a
  // provider alone is not proof that the model executed on an accelerator.
  auto profile=session.EndProfilingAllocated(allocator);check(profile.get()!=nullptr,"profile missing");
  return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
