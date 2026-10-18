#include "endpoint.h"
#include "frontend.h"
#include "onnxruntime_cxx_api.h"
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
void error_text(char* out, size_t cap, const char* message) noexcept {
  if (!out || !cap) return;
  const size_t n = std::min(cap - 1, std::strlen(message));
  std::memcpy(out, message, n); out[n] = 0;
}
}
struct AiiEndpoint {
  std::vector<unsigned char> model;
  aii::endpoint::Frontend frontend;
  Ort::Env env{ORT_LOGGING_LEVEL_WARNING, "aii-native-endpoint"};
  Ort::SessionOptions options;
  Ort::Session session{nullptr};
  std::atomic<bool> active{false};
  std::atomic<int> phase{0};
  std::atomic<uint64_t> cancelled{0};
  std::mutex publication;
  OrtRunOptions* running = nullptr;
  uint64_t running_id = 0, last_started = 0;
  bool faulted = false;
  AiiEndpoint(const void* data, size_t bytes, const float* coefficients, size_t count)
      : model(static_cast<const unsigned char*>(data), static_cast<const unsigned char*>(data)+bytes),
        frontend(coefficients, count) {
    env.DisableTelemetryEvents();
    options.SetIntraOpNumThreads(1); options.SetInterOpNumThreads(1);
    options.SetExecutionMode(ORT_SEQUENTIAL);
    session = Ort::Session(env, model.data(), model.size(), options);
    Ort::AllocatorWithDefaultOptions allocator;
    if (session.GetInputCount() != 1 || session.GetOutputCount() != 1)
      throw std::runtime_error("endpoint graph arity differs");
    const auto owner = session.GetInputTypeInfo(0);
    const auto info = owner.GetTensorTypeAndShapeInfo();
    const auto out_owner = session.GetOutputTypeInfo(0);
    const auto out_info = out_owner.GetTensorTypeAndShapeInfo();
    if (std::string(session.GetInputNameAllocated(0, allocator).get()) != "input_features" ||
        info.GetElementType() != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT ||
        info.GetShape() != std::vector<int64_t>{-1,80,800} ||
        std::string(session.GetOutputNameAllocated(0, allocator).get()) != "logits" ||
        out_info.GetElementType() != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT ||
        out_info.GetShape() != std::vector<int64_t>{-1,1})
      throw std::runtime_error("endpoint graph input differs");
  }
};
AiiEndpoint* aii_endpoint_create(const void* model, size_t bytes, const float* coefficients,
    size_t count, char* error, size_t capacity) {
  try {
    const char* off = std::getenv("ORT_DISABLE_TELEMETRY");
    if (!off || std::strcmp(off, "1")) throw std::invalid_argument("ORT_DISABLE_TELEMETRY=1 required before initialization");
    if (!model || (bytes != 8679182 && bytes != 32411198))
      throw std::invalid_argument("endpoint checkpoint extent differs");
    return new AiiEndpoint(model, bytes, coefficients, count);
  } catch (const std::exception& e) { error_text(error, capacity, e.what()); return nullptr; }
  catch (...) { error_text(error, capacity, "endpoint creation fault"); return nullptr; }
}
int aii_endpoint_score(AiiEndpoint* e, uint64_t id, const float* pcm, size_t samples,
    double* probability, float* feature_output, size_t feature_capacity, char* error, size_t capacity) {
  if (!e || !id || !pcm || !samples || samples > 960000 || !probability ||
      (feature_output ? feature_capacity != 64000 : feature_capacity != 0)) {
    error_text(error, capacity, "invalid endpoint input/output"); return 1;
  }
  for (size_t i = 0; i < samples; ++i) if (!std::isfinite(pcm[i])) {
    error_text(error, capacity, "endpoint PCM not finite"); return 1;
  }
  bool idle = false;
  if (!e->active.compare_exchange_strong(idle, true)) return 5;
  struct Release { AiiEndpoint* e; ~Release() { e->phase.store(0); e->active.store(false); } } release{e};
  bool ran = false;
  try {
    if (e->faulted) throw std::runtime_error("endpoint owner faulted; recreate");
    if (id <= e->last_started) { error_text(error, capacity, "endpoint query id reused"); return 1; }
    if (id <= e->cancelled.load()) return 3;
    e->last_started = id;
    const auto stopped = [&] { return id <= e->cancelled.load(); };
    e->phase.store(1);
    auto features = e->frontend.features(pcm, samples, stopped);
    if (stopped()) return 3;
    const int64_t shape[] = {1,80,800};
    auto memory = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);
    auto tensor = Ort::Value::CreateTensor<float>(memory, features.data(), features.size(), shape, 3);
    const char* input[] = {"input_features"};
    Ort::AllocatorWithDefaultOptions allocator;
    const auto output_owner = e->session.GetOutputNameAllocated(0, allocator);
    const char* output[] = {output_owner.get()};
    Ort::RunOptions options;
    struct RunOwner { AiiEndpoint* e; ~RunOwner() {
      std::lock_guard<std::mutex> lock(e->publication); e->running = nullptr; e->running_id = 0;
    } } run_owner{e};
    { std::lock_guard<std::mutex> lock(e->publication);
      if (stopped()) return 3;
      e->running = options; e->running_id = id;
    }
    e->phase.store(2); ran = true;
    auto values = e->session.Run(options, input, &tensor, 1, output, 1);
    const auto info = values[0].GetTensorTypeAndShapeInfo();
    if (info.GetElementType() != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT || info.GetElementCount() != 1)
      throw std::runtime_error("endpoint graph output differs");
    const double p = values[0].GetTensorData<float>()[0];
    if (!std::isfinite(p) || p < 0 || p > 1) throw std::runtime_error("endpoint probability invalid");
    std::lock_guard<std::mutex> lock(e->publication);
    if (stopped()) return 3;
    if (feature_output) std::copy(features.begin(), features.end(), feature_output);
    *probability = p; return 0;
  } catch (const Ort::Exception& failure) {
    if (ran && id <= e->cancelled.load() && failure.GetOrtErrorCode() == ORT_FAIL &&
        !std::strcmp(failure.what(), "Exiting due to terminate flag being set to true.")) return 3;
    if (ran) e->faulted = true;
    error_text(error, capacity, failure.what()); return 4;
  } catch (const std::exception& failure) {
    if (!ran && id <= e->cancelled.load() && !std::strcmp(failure.what(), "endpoint cancelled")) return 3;
    if (ran) e->faulted = true;
    error_text(error, capacity, failure.what()); return 4;
  } catch (...) {
    if (ran) e->faulted = true;
    error_text(error, capacity, "endpoint scoring fault"); return 4;
  }
}
int aii_endpoint_cancel_through(AiiEndpoint* e, uint64_t id) {
  if (!e || !id) return 1;
  try {
    std::lock_guard<std::mutex> lock(e->publication);
    if (id > e->cancelled.load()) e->cancelled.store(id);
    if (e->running && e->running_id <= id) {
      auto* status = Ort::GetApi().RunOptionsSetTerminate(e->running);
      if (status) { Ort::GetApi().ReleaseStatus(status); return 4; }
    }
    return 0;
  } catch (...) { return 4; }
}
int aii_endpoint_phase(const AiiEndpoint* e) { return e ? e->phase.load() : 0; }
void aii_endpoint_destroy(AiiEndpoint* e) { delete e; }
