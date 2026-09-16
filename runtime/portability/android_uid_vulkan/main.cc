// Private whole-utterance PCM qualification, not an installed session owner.
#include "bounded.h"
#include "gpu.h"
#include "net.h"
#include "simam.h"
#include "uid_frontend.h"
#include <chrono>
#include <fcntl.h>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <set>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>
using Clock = std::chrono::steady_clock;
double seconds(Clock::time_point t) {
  return std::chrono::duration<double>(Clock::now() - t).count();
}
struct File {
  int fd;
  explicit File(int n) : fd(n) {
    if (n < 0)
      throw std::runtime_error("file open refused");
  }
  ~File() { close(fd); }
};
std::vector<uint8_t> bytes(const std::string &p, size_t minimum,
                           size_t maximum) {
  File f(open(p.c_str(), O_RDONLY | O_CLOEXEC | O_NONBLOCK | O_NOFOLLOW));
  struct stat st{};
  if (fstat(f.fd, &st) || !S_ISREG(st.st_mode) ||
      st.st_size < int64_t(minimum) || st.st_size > int64_t(maximum))
    throw std::runtime_error("input bounds/type");
  std::vector<uint8_t> b(st.st_size);
  size_t i = 0;
  while (i < b.size()) {
    auto n = read(f.fd, b.data() + i, b.size() - i);
    if (n <= 0)
      throw std::runtime_error("short input");
    i += n;
  }
  uint8_t tail;
  if (read(f.fd, &tail, 1) != 0)
    throw std::runtime_error("input changed");
  return b;
}
void save(const std::string &p, const float *v, size_t count) {
  File f(open(p.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600));
  auto ptr = reinterpret_cast<const uint8_t *>(v);
  size_t left = count * 4;
  while (left) {
    auto n = write(f.fd, ptr, left);
    if (n <= 0)
      throw std::runtime_error("output write");
    ptr += n;
    left -= n;
  }
}
std::vector<std::string> cases(const std::string &p) {
  auto b = bytes(p, 1, 16384);
  std::vector<std::string> ids;
  std::set<std::string> seen;
  std::string id;
  for (auto c : b) {
    if (c == '\n') {
      if (id.empty() || !seen.insert(id).second || ids.size() >= 161)
        throw std::runtime_error("case set");
      ids.push_back(id);
      id.clear();
    } else {
      if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') ||
            (c >= 'A' && c <= 'Z') || c == '-' || c == '_') ||
          id.size() >= 96)
        throw std::runtime_error("unsafe case");
      id += char(c);
    }
  }
  if (!id.empty() || ids.empty())
    throw std::runtime_error("case terminator");
  return ids;
}
int main(int argc, char **argv) {
  try {
    bool trace = argc == 6 && std::string(argv[5]) == "trace-first";
    if (argc != 5 && !trace)
      throw std::runtime_error("MODEL_DIR INPUT_DIR NEW_OUTPUT_DIR cpu|vulkan");
    std::string model = argv[1], input = argv[2], output = argv[3],
                mode = argv[4];
    if (mode != "cpu" && mode != "vulkan")
      throw std::runtime_error("backend");
    auto ids = cases(input + "/cases.txt");
    if (trace)
      ids.resize(1);
    if (mkdir(output.c_str(), 0700))
      throw std::runtime_error("fresh output required");
    if (mode == "vulkan" && ncnn::get_gpu_count() != 1)
      throw std::runtime_error("one real Vulkan GPU required");
    BoundedAllocator allocator;
    std::unique_ptr<BoundedVkAllocator> gpu_allocator;
    if (mode == "vulkan")
      gpu_allocator.reset(new BoundedVkAllocator(ncnn::get_gpu_device(0)));
    ncnn::Net net;
    net.opt.num_threads = 2;
    net.opt.use_vulkan_compute = mode == "vulkan";
    net.opt.use_fp16_packed = net.opt.use_fp16_storage =
        net.opt.use_fp16_arithmetic = false;
    net.opt.use_bf16_storage = net.opt.use_int8_inference =
        net.opt.use_int8_storage = net.opt.use_int8_arithmetic = false;
    net.opt.use_winograd_convolution = false;
    net.opt.use_tensor_storage = false;
    net.opt.blob_allocator = net.opt.workspace_allocator = &allocator;
    if (mode == "vulkan")
      net.set_vulkan_device(0);
    net.register_custom_layer("AiiSimAMEnergy", AiiSimAMEnergy_layer_creator);
    auto start = Clock::now();
    if (net.load_param((model + "/model.ncnn.param").c_str()) ||
        net.load_model((model + "/model.ncnn.bin").c_str()))
      throw std::runtime_error("model/pipeline load");
    std::cout << std::setprecision(17)
              << "{\"event\":\"ready\",\"backend_requested\":\"" << mode
              << "\",\"setup_seconds\":" << seconds(start)
              << ",\"fp32\":true}\n"
              << std::flush;
    for (auto *layer : net.layers())
      std::cerr << "UID_LAYER " << layer->type << " " << layer->name
                << " vulkan=" << layer->support_vulkan << "\n";
    for (const auto &id : ids) {
      auto pcm = bytes(input + "/" + id + ".pcm16", 63840, 960000);
      std::vector<float> feat(3000 * 80);
      size_t frames = 0;
      start = Clock::now();
      int rc = aiii_uid_fbank(pcm.data(), pcm.size(), 16000, feat.data(),
                              feat.size(), &frames, nullptr, nullptr);
      double frontend = seconds(start);
      if (rc)
        throw std::runtime_error("native frontend " + std::to_string(rc));
      ncnn::Mat in(80, int(frames), feat.data(), size_t(4));
      ncnn::Mat out;
      auto ex = net.create_extractor();
      if (trace)
        ex.set_light_mode(false);
      if (gpu_allocator) {
        ex.set_blob_vkallocator(gpu_allocator.get());
        ex.set_workspace_vkallocator(gpu_allocator.get());
      }
      std::cerr << "UID_BEGIN " << id << "\n" << std::flush;
      if (trace) {
        if (ex.input("in0", in))
          throw std::runtime_error("trace input");
        for (const char *name :
             {"1", "2", "3", "6", "7", "12", "14", "17", "18", "19", "22"}) {
          ncnn::Mat value;
          if (ex.extract(name, value) || value.elempack != 1 ||
              value.elemsize != 4)
            throw std::runtime_error("trace extraction");
          std::vector<float> flat;
          for (int c = 0; c < value.c; ++c) {
            const float *v = value.channel(c);
            flat.insert(flat.end(), v, v + value.w * value.h * value.d);
          }
          save(output + "/blob-" + name + ".f32", flat.data(), flat.size());
          std::cout << "{\"event\":\"debug_tensor\",\"blob\":\"" << name
                    << "\",\"dims\":" << value.dims << ",\"w\":" << value.w
                    << ",\"h\":" << value.h << ",\"c\":" << value.c << "}\n"
                    << std::flush;
        }
        std::cout << "{\"event\":\"debug_complete\",\"qualified\":false}\n";
        return 0;
      }
      start = Clock::now();
      if (ex.input("in0", in) || ex.extract("out0", out))
        throw std::runtime_error("inference");
      double modeltime = seconds(start);
      std::cerr << "UID_END " << id << "\n" << std::flush;
      if (allocator.refusals)
        throw std::runtime_error("allocation refused");
      if (out.total() * out.elempack != 256 ||
          out.elemsize != size_t(4 * out.elempack))
        throw std::runtime_error("output shape/precision");
      const float *raw = out;
      std::vector<float> v(raw, raw + 256);
      double norm = 0;
      for (float x : v) {
        if (!std::isfinite(x))
          throw std::runtime_error("nonfinite");
        norm += double(x) * x;
      }
      if (norm <= 0)
        throw std::runtime_error("empty embedding");
      for (float &x : v)
        x = float(x / std::sqrt(norm));
      save(output + "/" + id + ".f32", feat.data(), frames * 80);
      save(output + "/" + id + ".embedding.f32", v.data(), 256);
      std::cout << "{\"event\":\"record\",\"id\":\"" << id
                << "\",\"pcm_bytes\":" << pcm.size() << ",\"frames\":" << frames
                << ",\"frontend_seconds\":" << frontend
                << ",\"model_seconds\":" << modeltime << "}\n"
                << std::flush;
    }
    std::cout << "{\"event\":\"complete\",\"records\":" << ids.size()
              << ",\"cpu_allocation_peak_bytes\":" << allocator.peak
              << ",\"allocation_refusals\":" << allocator.refusals
              << ",\"qualified\":false}\n";
    return 0;
  } catch (const std::exception &e) {
    std::cerr << "UID_REFUSED " << e.what() << "\n";
    return 1;
  }
}
