#include "asr.h"
#include "frontend.h"
#include "onnxruntime_cxx_api.h"
#include "initializers.h"
#include "../native/platform/startup_trace.h"
#include "../native/platform/coreml_candidate.h"
#include "execution.h"
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstring>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <memory>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
struct Cancelled {};
void error_text(char* dst, size_t capacity, const char* text) noexcept {
  if (dst && capacity) {
    const size_t size = std::min(std::strlen(text), capacity - 1);
    std::memcpy(dst, text, size); dst[size] = 0;
  }
}
struct Graph {
  Ort::Session session{nullptr};
  std::vector<std::string> in, out;
  std::vector<const char*> inputs, outputs;
  Graph(Ort::Env& env, const aii::platform::ReadonlyModel& bytes, Ort::SessionOptions& options)
      : session(env, bytes.data(), bytes.size(), options) {
    Ort::AllocatorWithDefaultOptions allocator;
    for (size_t i = 0; i < session.GetInputCount(); ++i)
      in.emplace_back(session.GetInputNameAllocated(i, allocator).get());
    for (size_t i = 0; i < session.GetOutputCount(); ++i)
      out.emplace_back(session.GetOutputNameAllocated(i, allocator).get());
    for (const auto& s : in) inputs.push_back(s.c_str());
    for (const auto& s : out) outputs.push_back(s.c_str());
  }
  std::vector<Ort::Value> run(std::vector<Ort::Value>& values) {
    if (values.size() != inputs.size()) throw std::runtime_error("graph input census");
    return session.Run(Ort::RunOptions{nullptr}, inputs.data(), values.data(), values.size(),
                       outputs.data(), outputs.size());
  }
};
struct Model {
  Ort::Env env{ORT_LOGGING_LEVEL_WARNING, "aii-native-asr"};
  std::unique_ptr<aii::platform::ReadonlyModel> decoder_bytes,joiner_bytes,token_bytes;
  Ort::SessionOptions options;
  std::unique_ptr<Graph> encoder, decoder, joiner;
  std::vector<float> mel;
  std::vector<std::string> symbols;
  std::string execution;
  Model(const char* root, const float* filters, size_t count, int threads, const aii::asr::ExecutionPolicy& policy) {
    if (!root || !*root || threads < 1 || threads > 16) throw std::invalid_argument("model options");
    aii::asr::Frontend validate(filters, count);
    mel.assign(filters, filters + count);
    env.DisableTelemetryEvents();
    options.SetIntraOpNumThreads(threads);
    options.SetInterOpNumThreads(1);
    options.SetExecutionMode(ORT_SEQUENTIAL);
    options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_ALL);
#ifdef AII_ASR_MOBILE_FLAT_ALLOCATOR
    // Explicit mobile candidate: avoid allocator arenas and packed duplicate
    // weights at initialization. Same graph/weights; separately measured before
    // any adoption. Desktop compilation does not define this switch.
    options.DisableCpuMemArena();
    options.AddConfigEntry("session.use_device_allocator_for_initializers","1");
    options.AddConfigEntry("session.disable_prepacking","1");
#endif
    if(policy.target_default)aii::platform::coreml_candidate(options,"asr");
    auto encoder_options=options.Clone();
    {
      aii::platform::StartupSpan span("execution_selection","native-asr-startup-profile");
      execution=aii::asr::configure_encoder(encoder_options,policy);
    }
    const auto dir = std::filesystem::u8path(root);
    const auto timed=[](const char* phase,auto action) {
      aii::platform::StartupSpan span(phase,"native-asr-startup-profile");
      return action();
    };
    // AddExternalInitializers copies into ORT's graph during construction.
    // The original mappings are construction-local: encoder_options is
    // destroyed first, then they are unmapped after the integrity checks.
    // Retaining them with Model pins a second checkpoint-sized resident view.
    auto initializers=timed("initializer_mapping_and_validation",[&]{return std::make_unique<aii::asr::Initializers>(dir);});
    const auto load=[&](const char* name,const aii::asr::Pin& pin){return std::make_unique<aii::platform::ReadonlyModel>(dir/name,pin.bytes,pin.sha);};
    decoder_bytes=timed("decoder_mapping_and_hash",[&]{return load("decoder.onnx",aii::asr::decoder_binding());});
    joiner_bytes=timed("joiner_mapping_and_hash",[&]{return load("joiner.onnx",aii::asr::joiner_binding());});
    token_bytes=timed("vocabulary_mapping_and_hash",[&]{return load("tokens.txt",aii::asr::tokens_binding());});
    initializers->attach(encoder_options);
    encoder = timed("encoder_session_build",[&]{return std::make_unique<Graph>(env, initializers->encoder, encoder_options);});
    decoder = timed("decoder_session_build",[&]{return std::make_unique<Graph>(env, *decoder_bytes, options);});
    joiner = timed("joiner_session_build",[&]{return std::make_unique<Graph>(env, *joiner_bytes, options);});
    aii::platform::StartupSpan validation("geometry_and_vocabulary_validation","native-asr-startup-profile");
    initializers->check_unchanged();decoder_bytes->check_unchanged();joiner_bytes->check_unchanged();token_bytes->check_unchanged();
    const std::pair<const char*, const char*> metadata[] = {
      {"window_size","65"}, {"chunk_shift","56"}, {"feat_dim","128"},
      {"pred_rnn_layers","2"}, {"pred_hidden","640"},
      {"cache_last_channel_dim1","24"}, {"cache_last_channel_dim2","56"},
      {"cache_last_channel_dim3","1024"}, {"cache_last_time_dim3","8"}};
    Ort::AllocatorWithDefaultOptions allocator;
    auto meta = encoder->session.GetModelMetadata();
    for (const auto& [key, expected] : metadata) {
      auto value = meta.LookupCustomMetadataMapAllocated(key, allocator);
      if (!value || std::string(value.get()) != expected) throw std::runtime_error("model geometry changed");
    }
    if (encoder->in.size() != 7 || encoder->in.back() != "diagnostic_drop" ||
        encoder->out.size() != 5 || decoder->in.size() != 4 || decoder->out.size() != 4 ||
        joiner->in != std::vector<std::string>{"encoder_outputs", "decoder_outputs"} || joiner->out.size() != 1)
      throw std::runtime_error("graph signature changed");
    std::istringstream file(std::string(reinterpret_cast<const char*>(token_bytes->data()),token_bytes->size()));
    symbols.resize(13088);
    std::vector<bool> seen(symbols.size(), false);
    std::string line;
    const std::regex language("<[a-z]{2,3}(-[A-Z]{2})?>");
    size_t lines = 0;
    while (std::getline(file, line)) {
      const auto split = line.rfind(' ');
      if (split == std::string::npos) throw std::runtime_error("vocabulary row");
      size_t used = 0;
      const auto id = std::stoul(line.substr(split+1), &used);
      if (used != line.size()-split-1 || id >= symbols.size() || seen[id])
        throw std::runtime_error("vocabulary identity");
      seen[id] = true; ++lines;
      auto symbol = line.substr(0, split);
      // <blk> fits the language-tag spelling too; validate/preserve the
      // reserved blank separately, and never emit it from the greedy loop.
      if (id != 13087 && std::regex_match(symbol, language)) symbol.clear();
      const std::string space = "\xe2\x96\x81";
      size_t at = 0;
      while ((at = symbol.find(space, at)) != std::string::npos) symbol.replace(at++, space.size(), " ");
      symbols[id] = symbol;
    }
    if (!file.eof() || lines != 13088 || symbols[13087] != "<blk>")
      throw std::runtime_error("incomplete vocabulary");
  }
};
template<class T> Ort::Value tensor(std::vector<T>& data, std::initializer_list<int64_t> dimensions) {
  auto info = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);
  std::vector<int64_t> shape(dimensions);
  size_t count = 1;
  for (auto d : shape) count *= static_cast<size_t>(d);
  if (count != data.size()) throw std::runtime_error("tensor extent");
  return Ort::Value::CreateTensor<T>(info, data.data(), data.size(), shape.data(), shape.size());
}
void shape(const Ort::Value& value, std::initializer_list<int64_t> expected,
           ONNXTensorElementDataType type) {
  if (!value.IsTensor()) throw std::runtime_error("non-tensor model output");
  auto info = value.GetTensorTypeAndShapeInfo();
  if (info.GetElementType() != type || info.GetShape() != std::vector<int64_t>(expected))
    throw std::runtime_error("model output extent/type");
  if (type == ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT) {
    const float* data = value.GetTensorData<float>();
    for (size_t i = 0; i < info.GetElementCount(); ++i)
      if (!std::isfinite(data[i])) throw std::runtime_error("nonfinite model output");
  }
}
template<class T> void copy_value(const Ort::Value& value, std::vector<T>& out) {
  const auto* data = value.GetTensorData<T>();
  out.assign(data, data + value.GetTensorTypeAndShapeInfo().GetElementCount());
}
}
struct AiiAsr { std::shared_ptr<Model> model; };
struct AiiAsrStream {
  std::shared_ptr<Model> model;
  aii::asr::Frontend features;
  std::vector<float> channel = std::vector<float>(24*56*1024, 0);
  std::vector<float> time = std::vector<float>(24*1024*8, 0);
  std::vector<int64_t> cache_length{0};
  std::vector<float> h = std::vector<float>(2*640, 0), c = std::vector<float>(2*640, 0);
  std::vector<float> decoder;
  std::vector<int> tokens;
  size_t processed = 0, source_samples = 0, padding = 0;
  bool failed = false;
  std::atomic<bool> cancelled{false}, active{false};
  std::atomic<int> phase{0};
  explicit AiiAsrStream(std::shared_ptr<Model> m)
      : model(std::move(m)), features(model->mel.data(), model->mel.size()) {}
  bool ready() const {
    return processed == 0 ? features.frames_ready() >= 49 : processed + 49 <= features.frames_ready();
  }
  void check() const { if (cancelled.load(std::memory_order_acquire)) throw Cancelled{}; }
  void decode_token(int token) {
    check();
    std::vector<int32_t> id{token}, length{1};
    std::vector<Ort::Value> inputs;
    inputs.push_back(tensor(id, {1,1})); inputs.push_back(tensor(length, {1}));
    inputs.push_back(tensor(h, {2,1,640})); inputs.push_back(tensor(c, {2,1,640}));
    phase.store(3, std::memory_order_release);
    auto values = model->decoder->run(inputs);
    phase.store(1, std::memory_order_release);
    check();
    shape(values[0], {1,640,1}, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT);
    shape(values[1], {1}, ONNX_TENSOR_ELEMENT_DATA_TYPE_INT32);
    shape(values[2], {2,1,640}, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT);
    shape(values[3], {2,1,640}, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT);
    if (values[1].GetTensorData<int32_t>()[0] != 1) throw std::runtime_error("decoder length");
    copy_value(values[0], decoder); copy_value(values[2], h); copy_value(values[3], c);
  }
  void step() {
    check();
    const size_t count = processed == 0 ? 49 : 65;
    const size_t first = processed == 0 ? 0 : processed - 16;
    auto rows = features.frames(first, count);
    std::vector<float> input(rows.size());
    for (size_t f = 0; f < count; ++f)
      for (size_t m = 0; m < 128; ++m) input[m*count+f] = rows[f*128+m];
    std::vector<int64_t> length{static_cast<int64_t>(count)}, zero{0}, drop{processed == 0 ? 0 : 2};
    std::vector<Ort::Value> inputs;
    inputs.push_back(tensor(input, {1,128,static_cast<int64_t>(count)}));
    inputs.push_back(tensor(length, {1})); inputs.push_back(tensor(channel, {1,24,56,1024}));
    inputs.push_back(tensor(time, {1,24,1024,8})); inputs.push_back(tensor(cache_length, {1}));
    inputs.push_back(tensor(zero, {1})); inputs.push_back(tensor(drop, {1}));
    phase.store(2, std::memory_order_release);
    auto values = model->encoder->run(inputs);
    phase.store(1, std::memory_order_release);
    check();
    shape(values[0], {1,1024,7}, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT);
    shape(values[1], {1}, ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64);
    shape(values[2], {1,24,56,1024}, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT);
    shape(values[3], {1,24,1024,8}, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT);
    shape(values[4], {1}, ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64);
    if (values[1].GetTensorData<int64_t>()[0] != 7) throw std::runtime_error("encoder length");
    copy_value(values[2], channel); copy_value(values[3], time); copy_value(values[4], cache_length);
    processed += 56;
    if (decoder.empty()) decode_token(13087);
    const auto* encoder = values[0].GetTensorData<float>();
    for (size_t frame = 0; frame < 7; ++frame) {
      std::vector<float> column(1024);
      for (size_t k = 0; k < 1024; ++k) column[k] = encoder[k*7+frame];
      for (size_t symbol = 0; symbol < 10; ++symbol) {
        check();
        std::vector<Ort::Value> join;
        join.push_back(tensor(column, {1,1024,1})); join.push_back(tensor(decoder, {1,640,1}));
        phase.store(4, std::memory_order_release);
        auto scores = model->joiner->run(join);
        phase.store(1, std::memory_order_release);
        check();
        // Export is [batch, acoustic-time, label-time, vocabulary].
        shape(scores[0], {1,1,1,13088}, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT);
        const auto* data = scores[0].GetTensorData<float>();
        const int token = static_cast<int>(std::max_element(data, data+13088) - data);
        if (token == 13087) break;
        if (tokens.size() >= 65536) throw std::runtime_error("decoded token bound");
        tokens.push_back(token); decode_token(token);
      }
    }
    check();
    // The next recurrent step consumes frames [processed-16, processed+49).
    // Preserve its first pre-emphasis predecessor; no cache/token/clock reset.
    const size_t next = (processed-16)*160;
    features.discard_before(next > 256 ? next-257 : 0);
  }
};
namespace {
struct Owner {
  AiiAsrStream& stream;
  explicit Owner(AiiAsrStream* s) : stream(*require(s)) {
    if (stream.active.exchange(true, std::memory_order_acq_rel)) throw std::runtime_error("stream owner busy");
  }
  static AiiAsrStream* require(AiiAsrStream* s) {
    if (!s) throw std::invalid_argument("null stream");
    return s;
  }
  ~Owner() {
    stream.phase.store(0, std::memory_order_release);
    stream.active.store(false, std::memory_order_release);
  }
};
template<class F> int call(AiiAsrStream* s, char* err, size_t cap, F body, bool terminal = false) noexcept {
  error_text(err, cap, "");
  try {
    Owner owner(s);
    if (!terminal) {
      if (s->failed) throw std::runtime_error("stream faulted; retire it");
      s->check();
    }
    return body();
  } catch (const Cancelled&) { return 2; }
    catch (const std::exception& e) { error_text(err, cap, e.what()); return -1; }
    catch (...) { error_text(err, cap, "unknown native fault"); return -1; }
}
}
extern "C" {
AiiAsr* aii_asr_create(const char* root, const float* mel, size_t count, int threads, char* err, size_t cap) {
  return aii_asr_create_configured(root,mel,count,threads,nullptr,err,cap);
}
AiiAsr* aii_asr_create_configured(const char* root, const float* mel, size_t count, int threads,
    const char* execution, char* err, size_t cap) {
  error_text(err, cap, "");
  try {
    const auto* disabled = std::getenv("ORT_DISABLE_TELEMETRY");
    if (!disabled || std::strcmp(disabled, "1"))
      throw std::runtime_error("ORT_DISABLE_TELEMETRY=1 required before native initialization");
    const auto policy=aii::asr::ExecutionPolicy::read(execution);
    return new AiiAsr{std::make_shared<Model>(root, mel, count, threads, policy)};
  }
  catch (const std::exception& e) { error_text(err, cap, e.what()); return nullptr; }
  catch (...) { error_text(err, cap, "unknown create fault"); return nullptr; }
}
int aii_asr_execution_info(AiiAsr* m,char* out,size_t capacity,size_t* required,char* err,size_t cap) {
  error_text(err,cap,"");if(out&&capacity)out[0]=0;
  if(required)*required=0;
  try {
    if(!m||!required||(!out&&capacity))throw std::invalid_argument("model, buffer and required size needed");
    *required=m->model->execution.size()+1;
    if(capacity<*required)return 1;
    std::memcpy(out,m->model->execution.c_str(),*required);return 0;
  } catch(const std::exception& e){error_text(err,cap,e.what());return -1;}
  catch(...){error_text(err,cap,"unknown execution readback failure");return -1;}
}
void aii_asr_destroy(AiiAsr* m) { delete m; }
AiiAsrStream* aii_asr_stream_create(AiiAsr* m, char* err, size_t cap) {
  error_text(err, cap, "");
  try {
    if (!m) throw std::invalid_argument("null model");
    return new AiiAsrStream(m->model);
  } catch (const std::exception& e) { error_text(err, cap, e.what()); return nullptr; }
  catch (...) { error_text(err, cap, "unknown stream fault"); return nullptr; }
}
int aii_asr_accept(AiiAsrStream* s, const float* data, size_t count, char* err, size_t cap) {
  return call(s, err, cap, [&] {
    if (count > 16000ULL*30*60 - s->source_samples) throw std::overflow_error("source audio bound");
    s->features.accept(data, count); s->source_samples += count; return 0;
  });
}
int aii_asr_finish(AiiAsrStream* s, char* err, size_t cap) {
  return call(s, err, cap, [&] {
    if (s->features.finished() || !s->source_samples) throw std::runtime_error("empty or closed input");
    // This context belongs ONLY to inference; source_samples never includes it.
    std::vector<float> zeros(66*160, 0);
    s->features.accept(zeros.data(), zeros.size()); s->features.finish(); s->padding = zeros.size();
    return 0;
  });
}
int aii_asr_step(AiiAsrStream* s, char* err, size_t cap) {
  return call(s, err, cap, [&] {
    if (!s->ready()) return 0;
    s->phase.store(1, std::memory_order_release);
    try { s->step(); } catch (const Cancelled&) { throw; } catch (...) { s->failed = true; throw; }
    return 1;
  });
}
int aii_asr_result(AiiAsrStream* s, char* text, size_t capacity, char* err, size_t cap) {
  if (text && capacity) text[0] = 0;
  return call(s, err, cap, [&] {
    std::string result;
    for (auto token : s->tokens) result += s->model->symbols[token];
    const auto first = result.find_first_not_of(" \t\r\n"), last = result.find_last_not_of(" \t\r\n");
    result = first == std::string::npos ? "" : result.substr(first, last-first+1);
    if (!text || capacity <= result.size()) throw std::invalid_argument("text capacity; no truncation");
    s->check(); std::memcpy(text, result.c_str(), result.size()+1); return 0;
  });
}
int aii_asr_stats(AiiAsrStream* s, AiiAsrStats* out, char* err, size_t cap) {
  return call(s, err, cap, [&] {
    if (!out) throw std::invalid_argument("null stats");
    *out = {s->source_samples, s->padding, s->features.frames_ready(), s->processed, s->tokens.size(),
      s->features.finished(), s->features.finished() && !s->ready() && !s->failed && !s->cancelled.load(),
      s->cancelled.load(), 0, s->failed};
    return 0;
  }, true);
}
int aii_asr_buffer_stats(AiiAsrStream* s, AiiAsrBufferStats* out, char* err, size_t cap) {
  return call(s, err, cap, [&] {
    if (!out) throw std::invalid_argument("null buffer stats");
    *out = {s->features.first_sample(), s->features.retained_samples(), s->features.samples()};
    return 0;
  }, true);
}
int aii_asr_stream_destroy(AiiAsrStream* s, char* err, size_t cap) {
  error_text(err, cap, "");
  if (!s) return 0;
  if (s->active.load(std::memory_order_acquire)) { error_text(err, cap, "stream owner busy"); return -1; }
  delete s; return 0;
}
void aii_asr_cancel(AiiAsrStream* s) { if (s) s->cancelled.store(true, std::memory_order_release); }
int aii_asr_busy(AiiAsrStream* s) { return s && s->active.load(std::memory_order_acquire); }
int aii_asr_phase(AiiAsrStream* s) { return s ? s->phase.load(std::memory_order_acquire) : 0; }
const char* aii_asr_runtime_version(void) { return OrtGetApiBase()->GetVersionString(); }
}
