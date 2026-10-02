#import <UIKit/UIKit.h>

#include <coreml_provider_factory.h>
#include <onnxruntime_cxx_api.h>

#include <algorithm>
#include <array>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace {

struct NpyFloat32 {
  std::vector<int64_t> shape;
  std::vector<float> values;
};

std::vector<uint8_t> ReadBytes(const std::string &path) {
  std::ifstream input(path, std::ios::binary | std::ios::ate);
  if (!input)
    throw std::runtime_error("cannot open input: " + path);
  const auto end = input.tellg();
  if (end < 0)
    throw std::runtime_error("cannot determine input size: " + path);
  std::vector<uint8_t> bytes(static_cast<size_t>(end));
  input.seekg(0);
  input.read(reinterpret_cast<char *>(bytes.data()),
             static_cast<std::streamsize>(bytes.size()));
  if (!input)
    throw std::runtime_error("cannot read complete input: " + path);
  return bytes;
}

std::vector<int64_t> ParseShape(const std::string &header) {
  const auto shape_key = header.find("shape");
  const auto open = header.find('(', shape_key);
  const auto close = header.find(')', open);
  if (shape_key == std::string::npos || open == std::string::npos ||
      close == std::string::npos) {
    throw std::runtime_error("npy header lacks a shape tuple");
  }
  std::vector<int64_t> shape;
  size_t cursor = open + 1;
  while (cursor < close) {
    while (cursor < close && (header[cursor] == ' ' || header[cursor] == ','))
      ++cursor;
    if (cursor >= close)
      break;
    if (header[cursor] < '0' || header[cursor] > '9') {
      throw std::runtime_error(
          "npy shape contains a non-positive-integer token");
    }
    int64_t value = 0;
    while (cursor < close && header[cursor] >= '0' && header[cursor] <= '9') {
      value = value * 10 + (header[cursor] - '0');
      ++cursor;
    }
    if (value <= 0)
      throw std::runtime_error("npy shape dimensions must be positive");
    shape.push_back(value);
  }
  if (shape.empty())
    throw std::runtime_error("npy shape is empty");
  return shape;
}

NpyFloat32 ReadNpyFloat32(const std::string &path) {
  const auto bytes = ReadBytes(path);
  if (bytes.size() < 10 || bytes[0] != 0x93 || bytes[1] != 'N' ||
      bytes[2] != 'U' || bytes[3] != 'M' || bytes[4] != 'P' ||
      bytes[5] != 'Y') {
    throw std::runtime_error("input is not an npy file");
  }
  const uint8_t major = bytes[6];
  size_t header_offset = 0;
  size_t header_size = 0;
  if (major == 1) {
    header_offset = 10;
    header_size =
        static_cast<size_t>(bytes[8]) | (static_cast<size_t>(bytes[9]) << 8);
  } else if (major == 2 || major == 3) {
    if (bytes.size() < 12)
      throw std::runtime_error("truncated npy v2/v3 header");
    header_offset = 12;
    header_size = static_cast<size_t>(bytes[8]) |
                  (static_cast<size_t>(bytes[9]) << 8) |
                  (static_cast<size_t>(bytes[10]) << 16) |
                  (static_cast<size_t>(bytes[11]) << 24);
  } else {
    throw std::runtime_error("unsupported npy major version");
  }
  if (header_offset + header_size > bytes.size()) {
    throw std::runtime_error("npy header exceeds file size");
  }
  const std::string header(
      reinterpret_cast<const char *>(bytes.data() + header_offset),
      header_size);
  if (header.find("<f4") == std::string::npos &&
      header.find("|f4") == std::string::npos) {
    throw std::runtime_error("npy tensor must be little-endian float32");
  }
  if (header.find("fortran_order': True") != std::string::npos ||
      header.find("fortran_order\": true") != std::string::npos) {
    throw std::runtime_error("Fortran-order npy tensors are unsupported");
  }
  auto shape = ParseShape(header);
  size_t count = 1;
  for (const auto dimension : shape)
    count *= static_cast<size_t>(dimension);
  const size_t data_offset = header_offset + header_size;
  if (bytes.size() != data_offset + count * sizeof(float)) {
    throw std::runtime_error("npy payload size differs from shape");
  }
  std::vector<float> values(count);
  std::memcpy(values.data(), bytes.data() + data_offset, count * sizeof(float));
  return {std::move(shape), std::move(values)};
}

std::string Resource(const std::string &basename,
                     const std::string &extension) {
  NSString *base = [NSString stringWithUTF8String:basename.c_str()];
  NSString *ext = [NSString stringWithUTF8String:extension.c_str()];
  NSString *path = [[NSBundle mainBundle] pathForResource:base ofType:ext];
  if (path == nil)
    throw std::runtime_error("missing bundled resource: " + basename + "." +
                             extension);
  return std::string([path UTF8String]);
}

struct Metrics {
  double max_abs;
  double cosine;
};

struct RunResult {
  bool passed;
  std::string json;
};

Metrics Compare(std::vector<float> actual, std::vector<float> expected) {
  if (actual.size() != expected.size())
    throw std::runtime_error("embedding shape differs");
  double actual_squared = 0.0;
  double expected_squared = 0.0;
  for (size_t i = 0; i < actual.size(); ++i) {
    if (!std::isfinite(actual[i]) || !std::isfinite(expected[i])) {
      throw std::runtime_error("embedding contains nonfinite value");
    }
    actual_squared += static_cast<double>(actual[i]) * actual[i];
    expected_squared += static_cast<double>(expected[i]) * expected[i];
  }
  if (!(actual_squared > 0.0) || !(expected_squared > 0.0)) {
    throw std::runtime_error("embedding has zero norm");
  }
  const double actual_inverse = 1.0 / std::sqrt(actual_squared);
  const double expected_inverse = 1.0 / std::sqrt(expected_squared);
  double max_abs = 0.0;
  double cosine = 0.0;
  for (size_t i = 0; i < actual.size(); ++i) {
    const double a = actual[i] * actual_inverse;
    const double e = expected[i] * expected_inverse;
    max_abs = std::max(max_abs, std::abs(a - e));
    cosine += a * e;
  }
  return {max_abs, cosine};
}

bool Contains(const std::vector<std::string> &values,
              const std::string &wanted) {
  return std::find(values.begin(), values.end(), wanted) != values.end();
}

std::string JsonStringArray(const std::vector<std::string> &values) {
  std::ostringstream output;
  output << '[';
  for (size_t index = 0; index < values.size(); ++index) {
    if (index)
      output << ',';
    output << '\"' << values[index] << '\"';
  }
  output << ']';
  return output.str();
}

size_t CountJsonStringField(const std::string &json, const std::string &key,
                            const std::string &value) {
  const std::string needle = "\"" + key + "\"";
  size_t count = 0;
  size_t cursor = 0;
  while ((cursor = json.find(needle, cursor)) != std::string::npos) {
    cursor += needle.size();
    while (cursor < json.size() &&
           std::isspace(static_cast<unsigned char>(json[cursor])))
      ++cursor;
    if (cursor >= json.size() || json[cursor++] != ':')
      continue;
    while (cursor < json.size() &&
           std::isspace(static_cast<unsigned char>(json[cursor])))
      ++cursor;
    const std::string quoted = "\"" + value + "\"";
    if (json.compare(cursor, quoted.size(), quoted) == 0)
      ++count;
  }
  return count;
}

RunResult Execute() {
  const std::array<const char *, 4> known_ids = {
      "61-70968-0029", "61-70970-0038", "367-130732-0007", "367-293981-0005"};
  std::vector<const char *> ids(known_ids.begin(), known_ids.end());
  if (const char *requested = std::getenv("VF100_VECTOR_ID")) {
    const auto found = std::find_if(known_ids.begin(), known_ids.end(),
                                    [requested](const char *value) {
                                      return std::strcmp(requested, value) == 0;
                                    });
    if (found == known_ids.end())
      throw std::runtime_error("VF100_VECTOR_ID is not a pinned vector");
    ids.assign(1, *found);
  }
  const auto available_providers = Ort::GetAvailableProviders();
  if (!Contains(available_providers, "CoreMLExecutionProvider")) {
    throw std::runtime_error(
        "CoreMLExecutionProvider is absent from the linked ONNX Runtime build");
  }

  Ort::Env environment(ORT_LOGGING_LEVEL_WARNING, "aii-voice-vf100-coreml-gpu");
  Ort::SessionOptions options;
  options.SetIntraOpNumThreads(1);
  options.SetInterOpNumThreads(1);
  options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_ALL);

  const std::unordered_map<std::string, std::string> coreml_options = {
      // ORT 1.24.2's shipped header documents an obsolete value spelling
      // (MLComputeUnitsCPUAndGPU); the linked parser accepts CPUAndGPU.
      {kCoremlProviderOption_MLComputeUnits, "CPUAndGPU"},
      {kCoremlProviderOption_ModelFormat, "MLProgram"},
      {kCoremlProviderOption_EnableOnSubgraphs, "1"},
      {kCoremlProviderOption_ProfileComputePlan, "1"},
  };
  // There is deliberately no CPU fallback branch here. Failure to register or
  // create the Core ML session is a failed GPU probe, not a CPU success.
  options.AppendExecutionProvider("CoreML", coreml_options);

  NSString *profile_path = [NSTemporaryDirectory()
      stringByAppendingPathComponent:@"vf100-ort-profile"];
  options.EnableProfiling([profile_path UTF8String]);

  const auto model = Resource("model", "onnx");
  Ort::Session session(environment, model.c_str(), options);
  auto memory =
      Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);

  bool parity_passed = true;
  std::ostringstream records;
  for (size_t index = 0; index < ids.size(); ++index) {
    const std::string id(ids[index]);
    const auto feature = ReadNpyFloat32(Resource(id + ".fbank", "npy"));
    const auto expected = ReadNpyFloat32(Resource(id + ".embedding", "npy"));
    if (feature.shape.size() != 2 || feature.shape[1] != 80) {
      throw std::runtime_error("feature tensor must have shape [frames, 80]");
    }
    if (expected.shape.size() != 1 || expected.shape[0] != 256) {
      throw std::runtime_error("reference embedding must have shape [256]");
    }
    const std::array<int64_t, 3> input_shape = {1, feature.shape[0], 80};
    auto input = Ort::Value::CreateTensor<float>(
        memory, const_cast<float *>(feature.values.data()),
        feature.values.size(), input_shape.data(), input_shape.size());
    const char *input_names[] = {"feats"};
    const char *output_names[] = {"embs"};
    const auto start = std::chrono::steady_clock::now();
    auto outputs = session.Run(Ort::RunOptions{nullptr}, input_names, &input, 1,
                               output_names, 1);
    const auto stop = std::chrono::steady_clock::now();
    const auto output_shape = outputs[0].GetTensorTypeAndShapeInfo().GetShape();
    if (output_shape.size() != 2 || output_shape[0] != 1 ||
        output_shape[1] != 256) {
      throw std::runtime_error("embedding tensor must have shape [1, 256]");
    }
    const float *raw = outputs[0].GetTensorData<float>();
    const auto metrics =
        Compare(std::vector<float>(raw, raw + 256), expected.values);
    const bool passed = metrics.max_abs <= 0.0001 && metrics.cosine >= 0.99999;
    parity_passed = parity_passed && passed;
    if (index)
      records << ',';
    records << "{\"id\":\"" << id
            << "\",\"passed\":" << (passed ? "true" : "false")
            << ",\"max_abs_error\":" << metrics.max_abs
            << ",\"cosine\":" << metrics.cosine << ",\"inference_ns\":"
            << std::chrono::duration_cast<std::chrono::nanoseconds>(stop -
                                                                    start)
                   .count()
            << '}';
  }

  Ort::AllocatorWithDefaultOptions allocator;
  const auto profile_file = session.EndProfilingAllocated(allocator);
  if (!profile_file || !profile_file.get()[0]) {
    throw std::runtime_error("ONNX Runtime returned no profile file");
  }
  const auto profile_bytes = ReadBytes(profile_file.get());
  const std::string profile(profile_bytes.begin(), profile_bytes.end());
  const size_t coreml_events =
      CountJsonStringField(profile, "provider", "CoreMLExecutionProvider");
  const size_t cpu_events =
      CountJsonStringField(profile, "provider", "CPUExecutionProvider");
  const bool provider_assigned = coreml_events > 0;
  const bool passed = parity_passed && provider_assigned;

  std::ostringstream result;
  result << "{\"schema\":1,\"id\":\"vf100-ios-coreml-gpu-device-output\","
         << "\"target_id\":\"iphone-17-pro\","
         << "\"provider_requested\":\"CoreMLExecutionProvider\","
         << "\"compute_units_requested\":\"CPUAndGPU\","
         << "\"model_format_requested\":\"MLProgram\","
         << "\"available_providers\":" << JsonStringArray(available_providers)
         << ','
         << "\"provider_profile\":{\"coreml_node_events\":" << coreml_events
         << ",\"cpu_node_events\":" << cpu_events << "},"
         << "\"parity_passed\":" << (parity_passed ? "true" : "false") << ','
         << "\"provider_assignment_passed\":"
         << (provider_assigned ? "true" : "false") << ','
         << "\"passed\":" << (passed ? "true" : "false") << ",\"records\":["
         << records.str() << "]}";
  return {passed, result.str()};
}

} // namespace

@interface AppDelegate : UIResponder <UIApplicationDelegate>
@property(strong, nonatomic) UIWindow *window;
@end

@implementation AppDelegate
- (BOOL)application:(UIApplication *)application
    didFinishLaunchingWithOptions:(NSDictionary *)launchOptions {
  self.window = [[UIWindow alloc] initWithFrame:[UIScreen mainScreen].bounds];
  self.window.rootViewController = [[UIViewController alloc] init];
  self.window.backgroundColor = UIColor.blackColor;
  [self.window makeKeyAndVisible];
  dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
    @autoreleasepool {
      try {
        const auto result = Execute();
        fprintf(stderr, "AII_VOICE_GPU_RESULT %s\n", result.json.c_str());
        fflush(stderr);
        exit(result.passed ? 0 : 1);
      } catch (const std::exception &error) {
        fprintf(stderr, "AII_VOICE_GPU_ERROR %s\n", error.what());
        fflush(stderr);
        exit(1);
      }
    }
  });
  return YES;
}
@end

int main(int argc, char *argv[]) {
  @autoreleasepool {
    return UIApplicationMain(argc, argv, nil,
                             NSStringFromClass([AppDelegate class]));
  }
}
