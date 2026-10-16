#include "onnx_backend.h"
#include <onnxruntime_cxx_api.h>
#include <algorithm>
#include <cmath>
#include <filesystem>
#include <string>
#ifdef _WIN32
#include "bound_session.h"
#endif

namespace aii::multitalker {
namespace {
Ort::SessionOptions session_options() {
  Ort::SessionOptions options;
  options.SetIntraOpNumThreads(2);
  options.SetInterOpNumThreads(1);
  options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_DISABLE_ALL);
  // These graphs execute serially. Idle per-graph pools must not busy-wait
  // against the active graph or other applications on the same machine.
  options.AddConfigEntry("session.intra_op.allow_spinning","0");
  options.AddConfigEntry("session.inter_op.allow_spinning","0");
  return options;
}
Ort::Value floats(const float* values, size_t size, const std::vector<int64_t>& shape,
                  const Ort::MemoryInfo& memory) {
  return Ort::Value::CreateTensor<float>(memory, const_cast<float*>(values), size,
                                         shape.data(), shape.size());
}
const float* checked(const Ort::Value& value, const std::vector<int64_t>& shape) {
  auto info = value.GetTensorTypeAndShapeInfo();
  if (info.GetShape() != shape || info.GetElementType() != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT)
    throw std::runtime_error("unexpected model output signature");
  auto values = value.GetTensorData<float>();
  for (size_t i = 0; i < info.GetElementCount(); ++i)
    if (!std::isfinite(values[i])) throw std::runtime_error("nonfinite model output");
  return values;
}
void signature(const Ort::Session& session, const std::vector<std::string>& in,
               const std::vector<std::string>& out) {
  if (session.GetInputCount() != in.size() || session.GetOutputCount() != out.size())
    throw std::runtime_error("model signature census");
  Ort::AllocatorWithDefaultOptions allocator;
  for (size_t i = 0; i < in.size(); ++i)
    if (session.GetInputNameAllocated(i, allocator).get() != in[i])
      throw std::runtime_error("model input name");
  for (size_t i = 0; i < out.size(); ++i)
    if (session.GetOutputNameAllocated(i, allocator).get() != out[i])
      throw std::runtime_error("model output name");
}
}
struct OnnxBackend::Impl {
  Ort::Env env{ORT_LOGGING_LEVEL_ERROR, "multitalker-development"};
  Ort::MemoryInfo memory = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);
  Ort::RunOptions run;
  Ort::Session decoder{nullptr}, joiner{nullptr};
  explicit Impl(const std::string& root) {
    env.DisableTelemetryEvents();
    auto options=session_options();
#ifdef _WIN32
    decoder = bound_session(env,root,"asr_decoder",options);
    joiner = bound_session(env,root,"asr_joiner",options);
#else
    decoder = Ort::Session(env, (std::filesystem::path(root)/"asr_decoder"/"model.onnx").c_str(), options);
    joiner = Ort::Session(env, (std::filesystem::path(root)/"asr_joiner"/"model.onnx").c_str(), options);
#endif
    signature(decoder,{"tokens","hidden","cell"},{"predicted","next_hidden","next_cell"});
    signature(joiner,{"encoded","predicted"},{"logits"});
  }
};
OnnxBackend::OnnxBackend(const std::string& root) : p_(std::make_unique<Impl>(root)) {}
OnnxBackend::~OnnxBackend() = default;
Prediction OnnxBackend::predict(int64_t token, const State& state) {
  if (token < 0 || token > blank_token) throw std::invalid_argument("prediction token");
  const int64_t token_shape[]{1,1};
  std::vector<Ort::Value> inputs;
  inputs.push_back(Ort::Value::CreateTensor<int64_t>(p_->memory,&token,1,token_shape,2));
  inputs.push_back(floats(state.hidden.data(),recurrent_size,{2,1,640},p_->memory));
  inputs.push_back(floats(state.cell.data(),recurrent_size,{2,1,640},p_->memory));
  const char* in[]{"tokens","hidden","cell"};
  const char* out[]{"predicted","next_hidden","next_cell"};
  auto results = p_->decoder.Run(p_->run,in,inputs.data(),inputs.size(),out,3);
  Prediction prediction;
  std::copy_n(checked(results[0],{1,640,1}),prediction_width,prediction.values.begin());
  std::copy_n(checked(results[1],{2,1,640}),recurrent_size,prediction.next.hidden.begin());
  std::copy_n(checked(results[2],{2,1,640}),recurrent_size,prediction.next.cell.begin());
  return prediction;
}
int64_t OnnxBackend::classify(const float* frame, const Prediction& prediction) {
  std::vector<Ort::Value> inputs;
  inputs.push_back(floats(frame,encoder_width,{1,1024,1},p_->memory));
  inputs.push_back(floats(prediction.values.data(),prediction_width,{1,640,1},p_->memory));
  const char* in[]{"encoded","predicted"};
  const char* out[]{"logits"};
  auto results = p_->joiner.Run(p_->run,in,inputs.data(),inputs.size(),out,1);
  const auto values = checked(results[0],{1,1,1,1025});
  return std::max_element(values,values+1025)-values;
}
bool OnnxBackend::scores(const float* frame, const Prediction& prediction, std::array<float, blank_token + 1>& out) {
  std::vector<Ort::Value> inputs;
  inputs.push_back(floats(frame,encoder_width,{1,1024,1},p_->memory));
  inputs.push_back(floats(prediction.values.data(),prediction_width,{1,640,1},p_->memory));
  const char* in[]{"encoded","predicted"};
  const char* names[]{"logits"};
  auto results = p_->joiner.Run(p_->run,in,inputs.data(),inputs.size(),names,1);
  const auto values = checked(results[0],{1,1,1,1025});
  std::copy(values,values+1025,out.begin());
  return true;
}
void OnnxBackend::cancel() noexcept { try { p_->run.SetTerminate(); } catch (...) {} }
void OnnxBackend::reopen() { p_->run.UnsetTerminate(); }

struct EncoderWeights {
  Ort::Env env{ORT_LOGGING_LEVEL_ERROR,"multitalker-encoder-development"};
  Ort::Session session{nullptr};
  const std::string root,profile_prefix;
  const int threads,cuda_device;
  std::atomic<bool> running{false};
  explicit EncoderWeights(const std::string& directory,const EncoderExecution& execution)
      :root(directory),profile_prefix(execution.profile_prefix),threads(execution.threads),cuda_device(execution.cuda_device) {
    env.DisableTelemetryEvents();
    auto options=session_options();
    if(execution.threads<1 || execution.threads>16 || execution.cuda_device < -1 || execution.cuda_device>127)
      throw std::invalid_argument("encoder execution configuration");
    options.SetIntraOpNumThreads(execution.threads);
    if(execution.cuda_device>=0) {
      const auto providers=Ort::GetAvailableProviders();
      if(std::find(providers.begin(),providers.end(),"CUDAExecutionProvider")==providers.end())
        throw std::runtime_error("requested encoder CUDA provider is unavailable");
      OrtCUDAProviderOptionsV2* raw=nullptr;
      const auto& api=Ort::GetApi();
      Ort::ThrowOnError(api.CreateCUDAProviderOptions(&raw));
      std::unique_ptr<OrtCUDAProviderOptionsV2,decltype(api.ReleaseCUDAProviderOptions)> cuda(raw,api.ReleaseCUDAProviderOptions);
      const auto device=std::to_string(execution.cuda_device);
      // Power-of-two growth can exhaust a shared 4 GiB device during loading,
      // before cuDNN creates its execution streams. Grow by the actual request;
      // keep the same weights, math precision and explicit CUDA placement.
      const char* keys[]{"device_id","use_tf32","cudnn_conv_algo_search","cudnn_conv_use_max_workspace","arena_extend_strategy"};
      const char* values[]{device.c_str(),"0","HEURISTIC","0","kSameAsRequested"};
      Ort::ThrowOnError(api.UpdateCUDAProviderOptions(cuda.get(),keys,values,5));
      options.AppendExecutionProvider_CUDA_V2(*cuda);
      // ORT keeps shape bookkeeping on CPU even for a CUDA encoder. The
      // qualification profile must prove neural operators ran on CUDA;
      // provider registration alone is not a hardware execution claim.
    }
    if(!execution.profile_prefix.empty())
      options.EnableProfiling(std::filesystem::u8path(execution.profile_prefix).c_str());
#ifdef _WIN32
    session=bound_session(env,root,"asr_encoder",options);
#else
    session=Ort::Session(env,(std::filesystem::path(root)/"asr_encoder"/"model.onnx").c_str(),options);
#endif
    signature(session,{"embeddings","lengths","channel","temporal","valid","foreground","background"},
                      {"encoded","encoded_lengths","next_channel","next_temporal","next_valid"});
    if (session.GetInputTypeInfo(2).GetTensorTypeAndShapeInfo().GetShape()!=std::vector<int64_t>{24,-1,70,1024} ||
        session.GetInputTypeInfo(3).GetTensorTypeAndShapeInfo().GetShape()!=std::vector<int64_t>{24,-1,1024,8})
      throw std::runtime_error("different encoder cache geometry");
  }
};
struct OnnxEncoder::Impl {
  std::shared_ptr<EncoderWeights> weights;
  Ort::MemoryInfo memory = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator,OrtMemTypeDefault);
  Ort::RunOptions run;
  struct Cache {
    std::vector<float> channel, temporal;
    int64_t valid=0;
  };
  std::array<Cache,state_count> tracks;
  uint64_t epoch=0;
  bool faulted=false;
  std::atomic<bool> cancelled{false};
  explicit Impl(const std::string& root,const EncoderExecution& execution)
      :weights(execution.weights?execution.weights:OnnxEncoder::load_weights(root,execution)) {
    if(weights->root!=root || weights->threads!=execution.threads ||
       weights->cuda_device!=execution.cuda_device || weights->profile_prefix!=execution.profile_prefix)
      throw std::invalid_argument("shared encoder model binding differs");
  }
};
std::shared_ptr<EncoderWeights> OnnxEncoder::load_weights(const std::string& root,const EncoderExecution& execution) {
  return std::make_shared<EncoderWeights>(root,execution);
}
OnnxEncoder::OnnxEncoder(const std::string& root,const EncoderExecution& execution) : p_(std::make_unique<Impl>(root,execution)) {}
OnnxEncoder::~OnnxEncoder()=default;
void OnnxEncoder::reset(uint64_t epoch) {
  if (!epoch || epoch<=p_->epoch) throw std::invalid_argument("encoder epoch must advance");
  p_->run.UnsetTerminate(); p_->cancelled.store(false);
  p_->tracks={}; p_->faulted=false; p_->epoch=epoch;
}
void OnnxEncoder::cancel() noexcept {
  p_->cancelled.store(true);
  try { p_->run.SetTerminate(); } catch (...) {}
}
void OnnxEncoder::clone_track(uint64_t epoch,uint32_t source,uint32_t target) {
  if(!epoch || epoch!=p_->epoch || source>=state_count || target>=track_count || source==target ||
     !p_->tracks[target].channel.empty())throw std::invalid_argument("encoder clone lifecycle");
  if(p_->faulted || p_->cancelled.load())throw std::runtime_error("encoder epoch retired");
  p_->tracks[target]=p_->tracks[source];
}
std::vector<float> OnnxEncoder::push(uint64_t epoch, uint32_t track, const float* embeddings,
                                    size_t frames, size_t valid_frames, const float* foreground,
                                    const float* background, bool final_chunk) {
  if (!epoch || epoch!=p_->epoch || track>=state_count || !embeddings || !foreground || !background ||
      !frames || frames>128 || !valid_frames || valid_frames>frames)
    throw std::invalid_argument("encoder input extent or epoch");
  if (p_->faulted || p_->cancelled.load()) throw std::runtime_error("encoder epoch retired");
  for (size_t i=0;i<frames*encoder_width;++i)
    if (!std::isfinite(embeddings[i])) throw std::invalid_argument("nonfinite embedding");
  for (size_t i=0;i<frames;++i)
    if ((foreground[i]!=0 && foreground[i]!=1) || (background[i]!=0 && background[i]!=1))
      throw std::invalid_argument("speaker targets must be binary");
  auto& cache=p_->tracks[track];
  if (cache.channel.empty()) {
    cache.channel.resize(24*70*1024); cache.temporal.resize(24*1024*8);
  }
  int64_t length=static_cast<int64_t>(valid_frames);
  const int64_t one[]{1};
  std::vector<Ort::Value> inputs;
  inputs.push_back(floats(embeddings,frames*1024,{1,static_cast<int64_t>(frames),1024},p_->memory));
  inputs.push_back(Ort::Value::CreateTensor<int64_t>(p_->memory,&length,1,one,1));
  inputs.push_back(floats(cache.channel.data(),cache.channel.size(),{24,1,70,1024},p_->memory));
  inputs.push_back(floats(cache.temporal.data(),cache.temporal.size(),{24,1,1024,8},p_->memory));
  inputs.push_back(Ort::Value::CreateTensor<int64_t>(p_->memory,&cache.valid,1,one,1));
  inputs.push_back(floats(foreground,frames,{1,static_cast<int64_t>(frames)},p_->memory));
  inputs.push_back(floats(background,frames,{1,static_cast<int64_t>(frames)},p_->memory));
  const char* in[]{"embeddings","lengths","channel","temporal","valid","foreground","background"};
  const char* out[]{"encoded","encoded_lengths","next_channel","next_temporal","next_valid"};
  try {
    // Live and separated-source inference is serial. Refuse competing entry
    // rather than waiting behind a model lock or allocating a second workspace.
    if(p_->weights->running.exchange(true))throw std::runtime_error("shared encoder inference is already active");
    struct Retire {std::atomic<bool>& running;~Retire(){running.store(false);}} retired{p_->weights->running};
    auto results=p_->weights->session.Run(p_->run,in,inputs.data(),inputs.size(),out,5);
    if (p_->cancelled.load()) throw std::runtime_error("encoder cancelled");
    for (auto index : {1,4}) {
      auto info=results[index].GetTensorTypeAndShapeInfo();
      if (info.GetElementType()!=ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64 || info.GetShape()!=std::vector<int64_t>{1})
        throw std::runtime_error("encoder length signature");
    }
    auto shape=results[0].GetTensorTypeAndShapeInfo().GetShape();
    if (shape.size()!=3 || shape[0]!=1 || shape[1]!=1024 || shape[2]<=0 || shape[2]>128)
      throw std::runtime_error("encoder output geometry");
    const auto values=checked(results[0],shape);
    const auto n=results[1].GetTensorData<int64_t>()[0], next_valid=results[4].GetTensorData<int64_t>()[0];
    if (n<=0 || n>shape[2] || next_valid<0 || next_valid>70)
      throw std::runtime_error("encoder output length");
    auto channel_shape=results[2].GetTensorTypeAndShapeInfo().GetShape();
    if (channel_shape.size()!=4 || channel_shape[0]!=24 || channel_shape[1]!=1 ||
        channel_shape[2]<70 || channel_shape[2]>198 || channel_shape[3]!=1024)
      throw std::runtime_error("encoder channel cache geometry");
    const auto channel=checked(results[2],channel_shape);
    const auto temporal=checked(results[3],{24,1,1024,8});
    // Upstream streaming_post_process retains the last 70 channel frames.
    for (size_t layer=0;layer<24;++layer)
      std::copy_n(channel+(layer*channel_shape[2]+channel_shape[2]-70)*1024,70*1024,
                  cache.channel.begin()+layer*70*1024);
    std::copy_n(temporal,cache.temporal.size(),cache.temporal.begin()); cache.valid=next_valid;
    const auto kept=final_chunk ? n : std::min<int64_t>(n,14);
    std::vector<float> encoded(static_cast<size_t>(kept)*1024);
    for (int64_t frame=0;frame<kept;++frame)
      for (size_t dim=0;dim<1024;++dim) encoded[frame*1024+dim]=values[dim*shape[2]+frame];
    return encoded;
  } catch (...) { p_->faulted=true; throw; }
}

struct OnnxCapture::Impl {
  Ort::Env env{ORT_LOGGING_LEVEL_ERROR,"multitalker-capture"};
  Ort::MemoryInfo memory = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator,OrtMemTypeDefault);
  Ort::RunOptions run;
  Ort::Session asr{nullptr}, diar{nullptr}, classifier{nullptr};
  std::atomic<bool> cancelled{false};
  bool faulted = false;
  explicit Impl(const std::string& root,bool legacy) {
    env.DisableTelemetryEvents();
    auto options=session_options();
#ifdef _WIN32
    asr=bound_session(env,root,"asr_preencode",options);
    if(legacy) {
    diar=bound_session(env,root,"diar_preencode",options);
    classifier=bound_session(env,root,"diar_classifier",options);
    }
#else
    asr=Ort::Session(env,(std::filesystem::path(root)/"asr_preencode"/"model.onnx").c_str(),options);
    if(legacy) {
    diar=Ort::Session(env,(std::filesystem::path(root)/"diar_preencode"/"model.onnx").c_str(),options);
    classifier=Ort::Session(env,(std::filesystem::path(root)/"diar_classifier"/"model.onnx").c_str(),options);
    }
#endif
    signature(asr,{"features","lengths"},{"embeddings","encoded_lengths"});
    if(legacy) {
    signature(diar,{"features","lengths"},{"embeddings","encoded_lengths"});
    signature(classifier,{"embeddings","lengths"},{"probabilities"});
    }
  }
  void available() const {
    if(faulted || cancelled.load()) throw std::runtime_error("capture epoch retired");
  }
};
OnnxCapture::OnnxCapture(const std::string& root,bool legacy):p_(std::make_unique<Impl>(root,legacy)){}
OnnxCapture::~OnnxCapture()=default;
void OnnxCapture::cancel() noexcept {
  p_->cancelled.store(true);
  try { p_->run.SetTerminate(); } catch (...) {}
}
void OnnxCapture::reopen() {
  p_->run.UnsetTerminate(); p_->faulted=false; p_->cancelled.store(false);
}
CaptureEmbeddings OnnxCapture::preencode(const float* features,size_t frames,
                                         size_t valid,size_t drop,bool diarization) {
  p_->available();
  if(!features || !frames || frames>1024 || !valid || valid>frames || drop>2)
    throw std::invalid_argument("capture feature extent");
  for(size_t i=0;i<frames*128;++i)
    if(!std::isfinite(features[i]))throw std::invalid_argument("nonfinite capture features");
  int64_t length=static_cast<int64_t>(valid); const int64_t one[]{1};
  std::vector<Ort::Value> inputs;
  inputs.push_back(floats(features,frames*128,{1,static_cast<int64_t>(frames),128},p_->memory));
  inputs.push_back(Ort::Value::CreateTensor<int64_t>(p_->memory,&length,1,one,1));
  const char* in[]{"features","lengths"}; const char* out[]{"embeddings","encoded_lengths"};
  try {
    auto results=(diarization?p_->diar:p_->asr).Run(p_->run,in,inputs.data(),2,out,2);
    p_->available();
    const auto width=diarization?512:1024;
    const auto shape=results[0].GetTensorTypeAndShapeInfo().GetShape();
    if(shape.size()!=3 || shape[0]!=1 || shape[1]<=static_cast<int64_t>(drop) ||
       shape[1]>128 || shape[2]!=width)throw std::runtime_error("capture embedding geometry");
    auto info=results[1].GetTensorTypeAndShapeInfo();
    if(info.GetElementType()!=ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64 || info.GetShape()!=std::vector<int64_t>{1})
      throw std::runtime_error("capture length signature");
    const auto n=results[1].GetTensorData<int64_t>()[0];
    if(n<=static_cast<int64_t>(drop) || n>shape[1])throw std::runtime_error("capture embedding length");
    const auto values=checked(results[0],shape);
    return {{values+drop*width,values+shape[1]*width},static_cast<size_t>(shape[1])-drop,
            static_cast<size_t>(n)-drop};
  } catch(...) { p_->faulted=true; throw; }
}
std::vector<float> OnnxCapture::diarize(const std::vector<float>& embeddings) {
  p_->available();
  if(embeddings.empty() || embeddings.size()%512 || embeddings.size()>504*512)
    throw std::invalid_argument("capture diarization extent");
  for(float x:embeddings)if(!std::isfinite(x))throw std::invalid_argument("nonfinite diarization input");
  int64_t n=static_cast<int64_t>(embeddings.size()/512); const int64_t one[]{1};
  std::vector<Ort::Value> inputs;
  inputs.push_back(floats(embeddings.data(),embeddings.size(),{1,n,512},p_->memory));
  inputs.push_back(Ort::Value::CreateTensor<int64_t>(p_->memory,&n,1,one,1));
  const char* in[]{"embeddings","lengths"}; const char* out[]{"probabilities"};
  try {
    auto results=p_->classifier.Run(p_->run,in,inputs.data(),2,out,1);
    p_->available();
    const auto values=checked(results[0],{1,n,4});
    for(int64_t i=0;i<n*4;++i)if(values[i]<0 || values[i]>1)
      throw std::runtime_error("diarization probability range");
    return {values,values+n*4};
  } catch(...) { p_->faulted=true; throw; }
}
}
