#include "onnx_separator.h"
#include <onnxruntime_cxx_api.h>
#include <algorithm>
#include <cmath>
#include <filesystem>
#include <stdexcept>

namespace aii::multitalker {
struct OnnxSeparator::Impl {
  Ort::Env env{ORT_LOGGING_LEVEL_ERROR,"source-separation"};
  Ort::Session session{nullptr};
  Ort::RunOptions run;
  std::atomic<bool> cancelled{false},running{false};
  bool cuda_enabled=false;
  Impl(const void* graph,size_t bytes,int threads,int cuda,const std::string& profile) {
    if(!graph||!bytes||bytes>512u*1024*1024||threads<1||threads>64||cuda< -1||cuda>63)
      throw std::invalid_argument("separator model/execution bound");
    env.DisableTelemetryEvents();Ort::SessionOptions options;
    options.SetIntraOpNumThreads(threads);options.SetInterOpNumThreads(1);
    options.AddConfigEntry("session.intra_op.allow_spinning","0");
    options.AddConfigEntry("session.inter_op.allow_spinning","0");
    if(!profile.empty())options.EnableProfiling(std::filesystem::u8path(profile).c_str());
    if(cuda>=0) {
      cuda_enabled=true;
      const auto available=Ort::GetAvailableProviders();
      if(std::find(available.begin(),available.end(),"CUDAExecutionProvider")==available.end())
        throw std::runtime_error("separator CUDA provider unavailable");
      const auto& api=Ort::GetApi();OrtCUDAProviderOptionsV2* raw=nullptr;
      Ort::ThrowOnError(api.CreateCUDAProviderOptions(&raw));
      auto release=[&](OrtCUDAProviderOptionsV2* p){api.ReleaseCUDAProviderOptions(p);};
      std::unique_ptr<OrtCUDAProviderOptionsV2,decltype(release)> owned(raw,release);
      const auto device=std::to_string(cuda);
      const char* keys[]={"device_id","use_tf32","gpu_mem_limit","cudnn_conv_algo_search","cudnn_conv_use_max_workspace","use_ep_level_unified_stream","arena_extend_strategy"};
      // Measured five-second adapter bound; leave headroom for resident ASR
      // and TTS. This limits its arena, not total device memory. One EP stream
      // avoids retaining separate stream-bound allocations between turns.
      // Grow by the allocation request, not a power-of-two reservation that
      // can consume the capped arena before the model's next small tensor.
      const char* values[]={device.c_str(),"0","1610612736","HEURISTIC","0","1","kSameAsRequested"};
      Ort::ThrowOnError(api.UpdateCUDAProviderOptions(raw,keys,values,7));
      Ort::ThrowOnError(api.SessionOptionsAppendExecutionProvider_CUDA_V2(options,raw));
    }
    session=Ort::Session(env,graph,bytes,options);
    Ort::AllocatorWithDefaultOptions allocator;
    if(session.GetInputCount()!=1||session.GetOutputCount()!=1||
       std::string(session.GetInputNameAllocated(0,allocator).get())!="pcm"||
       std::string(session.GetOutputNameAllocated(0,allocator).get())!="sources")
      throw std::runtime_error("separator model signature");
  }
};
OnnxSeparator::OnnxSeparator(const void* graph,size_t bytes,int threads,int cuda,const std::string& profile)
  :p_(std::make_unique<Impl>(graph,bytes,threads,cuda,profile)){}
OnnxSeparator::~OnnxSeparator()=default;
std::string OnnxSeparator::provider() const {return p_->cuda_enabled?"CUDAExecutionProvider":"CPUExecutionProvider";}
void OnnxSeparator::open() {
  if(p_->running.load())throw std::runtime_error("separator inference has not retired");
  p_->run.UnsetTerminate();p_->cancelled.store(false);
}
void OnnxSeparator::cancel() noexcept {
  p_->cancelled.store(true);try{p_->run.SetTerminate();}catch(...){}
}
Waveforms OnnxSeparator::separate(const std::vector<float>& pcm) {
  if(pcm.size()<32000||pcm.size()>input_limit)throw std::invalid_argument("separator input extent");
  for(float x:pcm)if(!std::isfinite(x)||std::abs(x)>1)throw std::invalid_argument("separator input PCM");
  if(p_->running.exchange(true))throw std::runtime_error("separator concurrent inference");
  struct Retire {std::atomic<bool>& running;~Retire(){running.store(false);}} retired{p_->running};
  try {
    if(p_->cancelled.load())throw aii::voice::Cancelled("separator cancelled");
    auto memory=Ort::MemoryInfo::CreateCpu(OrtArenaAllocator,OrtMemTypeDefault);
    const int64_t shape[]={1,static_cast<int64_t>(pcm.size())};
    auto input=Ort::Value::CreateTensor<float>(memory,const_cast<float*>(pcm.data()),pcm.size(),shape,2);
    const char* inputs[]={"pcm"};const char* outputs[]={"sources"};
    auto result=p_->session.Run(p_->run,inputs,&input,1,outputs,1);
    if(p_->cancelled.load())throw aii::voice::Cancelled("separator cancelled");
    const auto info=result[0].GetTensorTypeAndShapeInfo();
    if(info.GetElementType()!=ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT||info.GetShape()!=std::vector<int64_t>{1,2,shape[1]})
      throw std::runtime_error("separator output shape");
    const auto* data=result[0].GetTensorData<float>();
    Waveforms raw{{std::vector<float>(data,data+pcm.size()),std::vector<float>(data+pcm.size(),data+2*pcm.size())}};
    auto normalized=normalize_sources(pcm,std::move(raw));
    if(p_->cancelled.load())throw aii::voice::Cancelled("separator cancelled");
    return normalized;
  }catch(const Ort::Exception&) {
    if(p_->cancelled.load())throw aii::voice::Cancelled("separator cancelled");
    throw;
  }
}
std::string OnnxSeparator::end_profile() {
  if(p_->running.load())throw std::runtime_error("separator inference has not retired");
  Ort::AllocatorWithDefaultOptions allocator;
  auto path=p_->session.EndProfilingAllocated(allocator);return path.get()?path.get():"";
}
}
