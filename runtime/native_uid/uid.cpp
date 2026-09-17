#include "uid.h"
#include "model_contract.h"
#include "uid_frontend.h"
#ifdef AII_UID_NCNN
#include "ncnn_backend.h"
#else
#include "cancel_result.h"
#include "onnxruntime_cxx_api.h"
#include "../native/platform/coreml_candidate.h"
#endif
#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <mutex>
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
#ifndef AII_UID_NCNN
void provider(Ort::SessionOptions& options, const std::string& name) {
  options.SetIntraOpNumThreads(2);
  options.SetInterOpNumThreads(1);
  options.SetExecutionMode(ORT_SEQUENTIAL);
  if (name == "cpu") return;
#ifdef AII_MOBILE_COREML_CANDIDATE
  if (name == "coreml-ane") { aii::platform::coreml_candidate(options,"uid"); return; }
#endif
  if (name != "cuda") throw std::invalid_argument("UID backend not implemented by this build");
  const auto available = Ort::GetAvailableProviders();
  if (std::find(available.begin(), available.end(), "CUDAExecutionProvider") == available.end())
    throw std::runtime_error("requested UID CUDA backend unavailable");
  const auto& api = Ort::GetApi();
  OrtCUDAProviderOptionsV2* raw = nullptr;
  Ort::ThrowOnError(api.CreateCUDAProviderOptions(&raw));
  auto release = [&](OrtCUDAProviderOptionsV2* p) { api.ReleaseCUDAProviderOptions(p); };
  std::unique_ptr<OrtCUDAProviderOptionsV2, decltype(release)> owner(raw, release);
  const char* keys[] = {"use_tf32", "gpu_mem_limit", "cudnn_conv_algo_search", "cudnn_conv_use_max_workspace"};
  const char* values[] = {"0", "1073741824", "HEURISTIC", "0"};
  Ort::ThrowOnError(api.UpdateCUDAProviderOptions(raw, keys, values, 4));
  Ort::ThrowOnError(api.SessionOptionsAppendExecutionProvider_CUDA_V2(options, raw));
}
#endif
}

struct AiiUid {
#ifdef AII_UID_NCNN
  uid_detail::NcnnBackend session;
#else
  std::vector<unsigned char> bytes;
  Ort::Env env{ORT_LOGGING_LEVEL_WARNING, "aii-native-uid"};
  Ort::SessionOptions options;
  Ort::Session session{nullptr};
#endif
  std::atomic<bool> active{false};
  std::atomic<int> phase{0};
  std::atomic<uint64_t> cancelled{0};
  std::mutex publication;
#ifndef AII_UID_NCNN
  OrtRunOptions* running = nullptr; // protected by publication; never owns it
  uint64_t running_id = 0;
#endif
  uint64_t last_started = 0; // embedding worker only
  bool faulted = false;
#ifdef AII_UID_NCNN
  AiiUid(const void* graph, size_t graph_size, const void* weights, size_t weight_size,
         const std::string& backend):session(graph,graph_size,weights,weight_size,backend) {}
#else
  AiiUid(const void* data, size_t size, const std::string& backend)
      : bytes(static_cast<const unsigned char*>(data), static_cast<const unsigned char*>(data) + size) {
    env.DisableTelemetryEvents();
    provider(options, backend);
    session = Ort::Session(env, bytes.data(), bytes.size(), options);
    if (session.GetInputCount() != 1 || session.GetOutputCount() != 1)
      throw std::runtime_error("UID graph arity changed");
    Ort::AllocatorWithDefaultOptions allocator;
    const auto input_type = session.GetInputTypeInfo(0);
    const auto output_type = session.GetOutputTypeInfo(0);
    const auto in = input_type.GetTensorTypeAndShapeInfo();
    const auto out = output_type.GetTensorTypeAndShapeInfo();
    const auto shape = in.GetShape();
    if (std::string(session.GetInputNameAllocated(0, allocator).get()) != "feats" ||
        std::string(session.GetOutputNameAllocated(0, allocator).get()) != "embs" ||
        in.GetElementType() != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT ||
        out.GetElementType() != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT ||
        shape != std::vector<int64_t>{-1, -1, 80} ||
        out.GetShape() != std::vector<int64_t>{-1, 256})
      throw std::runtime_error("UID full-context graph geometry changed");
  }
#endif
};

AiiUid* aii_uid_create(const void* data, size_t bytes, const char* backend, char* error, size_t cap) {
  try {
#ifdef AII_UID_NCNN
    (void)data; (void)bytes; (void)backend;
    throw std::invalid_argument("this UID build requires its bound native representation");
#else
    const char* disabled = std::getenv("ORT_DISABLE_TELEMETRY");
    if (!disabled || std::string(disabled) != "1")
      throw std::invalid_argument("ORT_DISABLE_TELEMETRY=1 required before initialization");
    if (!data || !aii::uid::model_extent_supported(bytes) || !backend)
      throw std::invalid_argument("UID checkpoint extent/backend missing");
    return new AiiUid(data, bytes, backend);
#endif
  } catch (const std::exception& e) { error_text(error, cap, e.what()); return nullptr; }
  catch (...) { error_text(error, cap, "UID creation failure"); return nullptr; }
}

AiiUid* aii_uid_create_ncnn(const void* graph, size_t graph_size,
    const void* weights, size_t weight_size, const char* backend, char* error, size_t cap) {
  try {
#ifdef AII_UID_NCNN
    if (!graph || !weights || !backend || graph_size!=aii::uid::ncnn_contract.graph_bytes ||
        weight_size!=aii::uid::ncnn_contract.weight_bytes)
      throw std::invalid_argument("bound native UID representation extent missing");
    return new AiiUid(graph,graph_size,weights,weight_size,backend);
#else
    (void)graph; (void)graph_size; (void)weights; (void)weight_size; (void)backend;
    throw std::invalid_argument("native UID representation unsupported by this build");
#endif
  } catch (const std::exception& e) { error_text(error,cap,e.what()); return nullptr; }
  catch (...) { error_text(error,cap,"native UID creation failure"); return nullptr; }
}

int aii_uid_embed(AiiUid* v, uint64_t id, const uint8_t* pcm, size_t bytes,
    int rate, double* output, size_t dimensions, char* error, size_t cap) {
  if (!v || !id || !pcm || !output || dimensions != 256 || rate != 16000 ||
      bytes % 2 || bytes < 31920 * 2 || bytes > 480000 * 2) {
    error_text(error, cap, "UID needs bounded complete mono PCM16 at 16 kHz"); return 1;
  }
  bool idle = false;
  if (!v->active.compare_exchange_strong(idle, true)) {
    error_text(error, cap, "UID embedding worker already active"); return 5;
  }
  struct Retire {
    AiiUid* v;
    ~Retire() { v->phase.store(0); v->active.store(false); }
  } retire{v};
  bool ran = false;
  try {
    if (v->faulted) throw std::runtime_error("UID inference owner faulted; recreate it");
    if (id <= v->last_started) { error_text(error, cap, "UID utterance id was already used"); return 1; }
    if (id <= v->cancelled.load()) { error_text(error, cap, "UID utterance cancelled"); return 3; }
    v->last_started = id;
    v->phase.store(1);
    const size_t expected = 1 + (bytes / 2 - 400) / 160;
    std::vector<float> features(expected * 80);
    struct Fence { AiiUid* v; uint64_t id; } fence{v, id};
    auto stopped = [](void* p) -> int {
      const auto* f = static_cast<Fence*>(p);
      return f->id <= f->v->cancelled.load();
    };
    size_t frames = 0;
    const int result = aiii_uid_fbank(pcm, bytes, rate, features.data(), features.size(), &frames, stopped, &fence);
    if (result) {
      error_text(error, cap, result == 2 ? "UID signal unusable" : result == 3 ? "UID utterance cancelled" : "UID frontend failed");
      return result;
    }
    if (frames != expected) throw std::runtime_error("UID full utterance was not preserved");
    if (stopped(&fence)) { error_text(error, cap, "UID utterance cancelled"); return 3; }
#ifdef AII_UID_NCNN
    v->phase.store(2);
    ran = true;
    const auto values = v->session.run(features,frames,[&] { return stopped(&fence)!=0; });
    const auto* raw = values.data();
#else
    const int64_t shape[] = {1, static_cast<int64_t>(frames), 80};
    auto memory = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);
    auto tensor = Ort::Value::CreateTensor<float>(memory, features.data(), features.size(), shape, 3);
    const char* in[] = {"feats"}; const char* out[] = {"embs"};
    Ort::RunOptions run_options;
    // The guard is destroyed before run_options, including exception paths.
    // Cancel may use this pointer only while holding the same short lock.
    struct RunOwner {
      AiiUid* v;
      ~RunOwner() {
        std::lock_guard<std::mutex> lock(v->publication);
        v->running = nullptr; v->running_id = 0;
      }
    } run_owner{v};
    {
      std::lock_guard<std::mutex> lock(v->publication);
      if (stopped(&fence)) { error_text(error, cap, "UID utterance cancelled"); return 3; }
      v->running = run_options; v->running_id = id;
    }
    v->phase.store(2);
    ran = true;
    auto values = v->session.Run(run_options, in, &tensor, 1, out, 1);
    const auto info = values[0].GetTensorTypeAndShapeInfo();
    if (info.GetElementType() != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT ||
        info.GetShape() != std::vector<int64_t>{1, 256})
      throw std::runtime_error("UID output geometry changed");
    const auto* raw = values[0].GetTensorData<float>();
#endif
    std::array<double, 256> result_vector{};
    double sum = 0;
    for (size_t i = 0; i < result_vector.size(); ++i) {
      if (!std::isfinite(raw[i])) throw std::runtime_error("UID embedding not finite");
      result_vector[i] = raw[i]; sum += result_vector[i] * result_vector[i];
    }
    const double norm = std::sqrt(sum);
    if (!std::isfinite(norm) || norm < 1e-12) throw std::runtime_error("UID embedding norm invalid");
    for (auto& x : result_vector) x /= norm;
    // This short lock linearizes publication against cancel. It is never held
    // across feature extraction, model inference or memory allocation.
    std::lock_guard<std::mutex> lock(v->publication);
    if (stopped(&fence)) { error_text(error, cap, "UID utterance cancelled"); return 3; }
    std::copy(result_vector.begin(), result_vector.end(), output);
    return 0;
#ifdef AII_UID_NCNN
  } catch (const uid_detail::NcnnCancelled& e) {
    error_text(error,cap,e.what()); return 3;
#else
  } catch (const Ort::Exception& e) {
    // ORT termination is a per-run request, not a mutation of model weights.
    // Preserve the actual diagnostic even when cancellation is the outcome.
    if (ran && uid_detail::run_was_cancelled(id <= v->cancelled.load(), e.GetOrtErrorCode(), e.what())) {
      error_text(error, cap, e.what()); return 3;
    }
    if (ran) v->faulted = true;
    error_text(error, cap, e.what()); return 4;
#endif
  } catch (const std::exception& e) {
    if (ran) v->faulted = true;
    error_text(error, cap, e.what()); return 4;
  } catch (...) {
    if (ran) v->faulted = true;
    error_text(error, cap, "UID embedding failure"); return 4;
  }
}

int aii_uid_cancel_through(AiiUid* v, uint64_t through) {
  if (!v || !through) return 1;
  try {
    std::lock_guard<std::mutex> lock(v->publication);
    if (through > v->cancelled.load()) v->cancelled.store(through);
#ifndef AII_UID_NCNN
    if (v->running && v->running_id <= through) {
      auto* status = Ort::GetApi().RunOptionsSetTerminate(v->running);
      if (status) { Ort::GetApi().ReleaseStatus(status); return 4; }
    }
#endif
    return 0;
  } catch (...) { return 4; }
}
int aii_uid_phase(const AiiUid* v) { return v ? v->phase.load() : 0; }
void aii_uid_destroy(AiiUid* v) { delete v; }
