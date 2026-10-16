// Isolated recorded-PCM qualification runner, not an audio/session owner.
// The caller hashes inputs/outputs independently. No expected embeddings or
// reference features are consumed by this executable.
#include "uid_frontend.h"
#include "onnxruntime_cxx_api.h"
#include "nnapi_provider_factory.h"
#include <android/NeuralNetworks.h>
#include <vulkan/vulkan.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <fcntl.h>
#include <iomanip>
#include <iostream>
#include <set>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

using Clock = std::chrono::steady_clock;
double elapsed(Clock::time_point t) {
  return std::chrono::duration<double>(Clock::now() - t).count();
}
std::string quoted(const char* s) {
  std::string out = "\"";
  for (const unsigned char* p = reinterpret_cast<const unsigned char*>(s); *p; ++p) {
    if (*p == '"' || *p == '\\') out += '\\';
    if (*p < 32) out += '?'; else out += char(*p);
  }
  return out + '"';
}
struct FD {
  int value;
  explicit FD(int v): value(v) { if (v < 0) throw std::runtime_error("file open refused"); }
  ~FD() { close(value); }
  FD(const FD&) = delete;
  FD& operator=(const FD&) = delete;
};
std::vector<uint8_t> read_bounded(const std::string& path, size_t low, size_t high) {
  FD fd(open(path.c_str(), O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC));
  struct stat st{};
  if (fstat(fd.value, &st) || !S_ISREG(st.st_mode) || st.st_size < int64_t(low) ||
      st.st_size > int64_t(high)) throw std::runtime_error("invalid bounded regular input");
  std::vector<uint8_t> out(static_cast<size_t>(st.st_size));
  size_t done = 0;
  while (done < out.size()) {
    auto n = read(fd.value, out.data() + done, out.size() - done);
    if (n <= 0) throw std::runtime_error("short read");
    done += size_t(n);
  }
  uint8_t tail;
  if (read(fd.value, &tail, 1) != 0) throw std::runtime_error("input changed while reading");
  return out;
}
void write_new(const std::string& path, const float* data, size_t count) {
  FD fd(open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600));
  const auto* ptr = reinterpret_cast<const uint8_t*>(data);
  size_t left = count * sizeof(float);
  while (left) {
    auto n = write(fd.value, ptr, left);
    if (n <= 0) throw std::runtime_error("output write refused");
    left -= size_t(n); ptr += n;
  }
}
std::vector<std::string> ids(const std::string& path) {
  const auto b = read_bounded(path, 1, 16384);
  std::vector<std::string> result;
  std::set<std::string> seen;
  std::string current;
  for (auto c : b) {
    if (c == '\n') {
      if (current.empty() || !seen.insert(current).second || result.size() >= 161)
        throw std::runtime_error("duplicate/empty/excess input id");
      result.push_back(current); current.clear();
    } else {
      if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
            (c >= '0' && c <= '9') || c == '-' || c == '_') || current.size() >= 96)
        throw std::runtime_error("unsafe input id");
      current += char(c);
    }
  }
  if (!current.empty() || result.empty()) throw std::runtime_error("incomplete case list");
  return result;
}
void devices() {
  uint32_t count = 0;
  int rc = ANeuralNetworks_getDeviceCount(&count);
  if (rc || count > 64) throw std::runtime_error("NNAPI enumeration failed");
  std::cout << "{\"nnapi_devices\":[";
  for (uint32_t i = 0; i < count; ++i) {
    ANeuralNetworksDevice* device = nullptr;
    const char *name = nullptr, *version = nullptr;
    int32_t type = 0;
    int64_t feature = 0;
    if (ANeuralNetworks_getDevice(i, &device) || ANeuralNetworksDevice_getName(device, &name) ||
        ANeuralNetworksDevice_getVersion(device, &version) ||
        ANeuralNetworksDevice_getType(device, &type) ||
        ANeuralNetworksDevice_getFeatureLevel(device, &feature))
      throw std::runtime_error("NNAPI device detail failed");
    if (i) std::cout << ',';
    std::cout << "{\"name\":" << quoted(name) << ",\"version\":" << quoted(version)
              << ",\"type\":" << type << ",\"feature_level\":" << feature << '}';
  }
  VkApplicationInfo app{};
  app.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
  app.apiVersion = VK_API_VERSION_1_0;
  VkInstanceCreateInfo create{};
  create.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO;
  create.pApplicationInfo = &app;
  VkInstance instance{};
  if (vkCreateInstance(&create, nullptr, &instance) != VK_SUCCESS)
    throw std::runtime_error("Vulkan instance unavailable");
  count = 0;
  if (vkEnumeratePhysicalDevices(instance, &count, nullptr) != VK_SUCCESS || count > 64)
    throw std::runtime_error("Vulkan enumeration failed");
  std::vector<VkPhysicalDevice> physical(count);
  if (vkEnumeratePhysicalDevices(instance, &count, physical.data()) != VK_SUCCESS)
    throw std::runtime_error("Vulkan enumeration changed");
  std::cout << "],\"vulkan_devices\":[";
  for (uint32_t i = 0; i < count; ++i) {
    VkPhysicalDeviceProperties p{};
    vkGetPhysicalDeviceProperties(physical[i], &p);
    if (i) std::cout << ',';
    std::cout << "{\"name\":" << quoted(p.deviceName) << ",\"api_version\":" << p.apiVersion
              << ",\"driver_version\":" << p.driverVersion << ",\"type\":" << p.deviceType << '}';
  }
  vkDestroyInstance(instance, nullptr);
  std::cout << "],\"model_executed\":false}\n";
}

int main(int argc, char** argv) {
  try {
    std::cout << std::setprecision(17);
    if (argc == 2 && std::string(argv[1]) == "--devices") { devices(); return 0; }
    if (argc != 5) throw std::runtime_error("MODEL INPUT_DIR FRESH_OUTPUT_DIR cpu|nnapi");
    const std::string model = argv[1], input = argv[2], output = argv[3], mode = argv[4];
    if (mode != "cpu" && mode != "nnapi") throw std::runtime_error("unknown backend");
    auto cases = ids(input + "/cases.txt");
    // Refuse an existing result directory: no stale result can satisfy a rerun.
    if (mkdir(output.c_str(), 0700)) throw std::runtime_error("fresh output directory required");
    Ort::Env env(ORT_LOGGING_LEVEL_WARNING, "uid-pcm");
    Ort::SessionOptions options;
    options.SetIntraOpNumThreads(2);
    options.SetInterOpNumThreads(1);
    options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_ALL);
    options.EnableProfiling((output + "/ort-profile").c_str());
    if (mode == "nnapi")
      Ort::ThrowOnError(OrtSessionOptionsAppendExecutionProvider_Nnapi(options, NNAPI_FLAG_CPU_DISABLED));
    const auto setup = Clock::now();
    Ort::Session session(env, model.c_str(), options);
    const double setup_seconds = elapsed(setup);
    auto memory = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);
    const char* inputs[] = {"feats"}; const char* outputs[] = {"embs"};
    std::cout << "{\"event\":\"ready\",\"backend_requested\":" << quoted(mode.c_str())
              << ",\"ort_version\":" << quoted(OrtGetApiBase()->GetVersionString())
              << ",\"setup_seconds\":" << setup_seconds << "}\n" << std::flush;
    for (const auto& id : cases) {
      auto pcm = read_bounded(input + "/" + id + ".pcm16", 31920*2, 480000*2);
      std::vector<float> feats(3000*80);
      size_t frames = 0;
      auto start = Clock::now();
      int rc = aiii_uid_fbank(pcm.data(), pcm.size(), 16000, feats.data(), feats.size(), &frames, nullptr, nullptr);
      const double frontend_seconds = elapsed(start);
      if (rc) throw std::runtime_error("native frontend refused: " + std::to_string(rc));
      int64_t shape[] = {1, int64_t(frames), 80};
      auto tensor = Ort::Value::CreateTensor<float>(memory, feats.data(), frames*80, shape, 3);
      start = Clock::now();
      auto result = session.Run(Ort::RunOptions{nullptr}, inputs, &tensor, 1, outputs, 1);
      const double model_seconds = elapsed(start);
      if (result.size() != 1 || result[0].GetTensorTypeAndShapeInfo().GetShape() != std::vector<int64_t>{1,256})
        throw std::runtime_error("embedding shape differs");
      const float* raw = result[0].GetTensorData<float>();
      std::vector<float> embedding(raw, raw+256);
      double norm = 0;
      for (float v : embedding) { if (!std::isfinite(v)) throw std::runtime_error("nonfinite embedding"); norm += double(v)*v; }
      if (norm <= 0) throw std::runtime_error("empty embedding");
      for (float& v : embedding) v = float(v / std::sqrt(norm));
      write_new(output + "/" + id + ".f32", feats.data(), frames*80);
      write_new(output + "/" + id + ".embedding.f32", embedding.data(), embedding.size());
      std::cout << "{\"event\":\"record\",\"id\":" << quoted(id.c_str())
                << ",\"pcm_bytes\":" << pcm.size() << ",\"frames\":" << frames
                << ",\"frontend_seconds\":" << frontend_seconds << ",\"model_seconds\":"
                << model_seconds << "}\n" << std::flush;
    }
    Ort::AllocatorWithDefaultOptions allocator;
    auto profile = session.EndProfilingAllocated(allocator);
    std::cout << "{\"event\":\"complete\",\"records\":" << cases.size()
              << ",\"profile\":" << quoted(profile.get()) << ",\"qualified\":false}\n";
    return 0;
  } catch (const std::exception& e) {
    std::cerr << "UID_REFUSED " << e.what() << '\n'; return 1;
  }
}
