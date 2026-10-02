#include "vad.h"
#include "onnxruntime_cxx_api.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
void error_text(char* dst, size_t cap, const char* value) noexcept {
  if (dst && cap) {
    const auto n = std::min(cap - 1, std::strlen(value));
    std::memcpy(dst, value, n); dst[n] = 0;
  }
}
}
struct AiiVad {
  // Retain serialized model storage for the complete session lifetime.
  std::vector<unsigned char> bytes;
  Ort::Env env{ORT_LOGGING_LEVEL_WARNING, "aii-native-vad"};
  Ort::SessionOptions options;
  Ort::Session session{nullptr};
  std::array<float,256> state{};
  std::array<float,64> context{};
  uint64_t samples = 0;
  bool failed = false;
  AiiVad(const void* data, size_t size)
    : bytes(static_cast<const unsigned char*>(data), static_cast<const unsigned char*>(data)+size) {
    env.DisableTelemetryEvents();
    options.SetIntraOpNumThreads(1);
    options.SetInterOpNumThreads(1);
    options.SetExecutionMode(ORT_SEQUENTIAL);
    session = Ort::Session(env, bytes.data(), bytes.size(), options);
    if (session.GetInputCount()!=3 || session.GetOutputCount()!=2)
      throw std::runtime_error("VAD graph signature changed");
    Ort::AllocatorWithDefaultOptions allocator;
    const char* names[] = {"input", "state", "sr"};
    for (size_t i=0;i<3;++i) {
      if (std::string(session.GetInputNameAllocated(i, allocator).get())!=names[i])
        throw std::runtime_error("VAD input name changed");
    }
    if (std::string(session.GetOutputNameAllocated(0, allocator).get())!="output" ||
        std::string(session.GetOutputNameAllocated(1, allocator).get())!="stateN")
      throw std::runtime_error("VAD output name changed");
  }
};

AiiVad* aii_vad_create(const void* data, size_t size, char* error, size_t cap) {
  try {
    const char* disabled=std::getenv("ORT_DISABLE_TELEMETRY");
    if (!disabled || std::string(disabled)!="1")
      throw std::invalid_argument("ORT_DISABLE_TELEMETRY=1 required before initialization");
    if (!data || size!=2243022) throw std::invalid_argument("VAD checkpoint extent");
    return new AiiVad(data,size);
  } catch (const std::exception& e) { error_text(error,cap,e.what()); return nullptr; }
  catch (...) { error_text(error,cap,"VAD creation failure"); return nullptr; }
}

int aii_vad_feed(AiiVad* v, const float* input, size_t n, float* probability, char* error, size_t cap) {
  try {
    if (!v || !input || !probability || n!=512) throw std::invalid_argument("VAD needs 512 finite 16 kHz mono samples");
    if (v->failed) throw std::runtime_error("VAD owner faulted; recreate it");
    if (v->samples>std::numeric_limits<uint64_t>::max()-512) throw std::overflow_error("VAD sample counter");
    for (size_t i=0;i<n;++i) if (!std::isfinite(input[i])) throw std::invalid_argument("VAD input not finite");
    std::array<float,576> signal;
    std::copy(v->context.begin(),v->context.end(),signal.begin());
    std::copy(input,input+n,signal.begin()+64);
    const int64_t audio_shape[]={1,576}, state_shape[]={2,1,128};
    int64_t sr=16000;
    auto memory=Ort::MemoryInfo::CreateCpu(OrtArenaAllocator,OrtMemTypeDefault);
    std::array<Ort::Value,3> values={
      Ort::Value::CreateTensor<float>(memory,signal.data(),signal.size(),audio_shape,2),
      Ort::Value::CreateTensor<float>(memory,v->state.data(),v->state.size(),state_shape,3),
      Ort::Value::CreateTensor<int64_t>(memory,&sr,1,nullptr,0)};
    const char* in[]={"input","state","sr"};
    const char* out[]={"output","stateN"};
    // Input validation is non-mutating. Once inference begins, a failure
    // poisons this owner instead of presenting an old state as a fresh result.
    v->failed=true;
    auto result=v->session.Run(Ort::RunOptions{nullptr},in,values.data(),3,out,2);
    const auto pi=result[0].GetTensorTypeAndShapeInfo();
    const auto si=result[1].GetTensorTypeAndShapeInfo();
    if (pi.GetElementType()!=ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT || pi.GetShape()!=std::vector<int64_t>{1,1} ||
        si.GetElementType()!=ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT || si.GetShape()!=std::vector<int64_t>{2,1,128})
      throw std::runtime_error("VAD output geometry changed");
    const auto p=result[0].GetTensorData<float>()[0];
    const auto* s=result[1].GetTensorData<float>();
    if (!std::isfinite(p) || p<0 || p>1) throw std::runtime_error("VAD probability invalid");
    for (size_t i=0;i<v->state.size();++i) if (!std::isfinite(s[i])) throw std::runtime_error("VAD state invalid");
    std::copy(s,s+v->state.size(),v->state.begin());
    std::copy(signal.end()-64,signal.end(),v->context.begin());
    v->samples+=512;
    v->failed=false;
    *probability=p;
    return 0;
  } catch (const std::exception& e) { error_text(error,cap,e.what()); return -1; }
  catch (...) { error_text(error,cap,"VAD inference failure"); return -1; }
}

int aii_vad_reset(AiiVad* v, char* error, size_t cap) {
  if (!v || v->failed) { error_text(error,cap,"VAD reset needs a healthy owner"); return -1; }
  v->state.fill(0); v->context.fill(0); v->samples=0;
  return 0;
}
uint64_t aii_vad_samples(const AiiVad* v) { return v?v->samples:0; }
void aii_vad_destroy(AiiVad* v) { delete v; }
