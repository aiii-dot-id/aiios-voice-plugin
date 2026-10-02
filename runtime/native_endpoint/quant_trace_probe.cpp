// Development-only original ONNX runtime oracle. No microphone, SDK, candidate
// selection or accelerator fallback. The runner binds every file before launch.
#include <onnxruntime_cxx_api.h>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

int main(int argc, char** argv) {
  try {
    if (argc != 4) throw std::runtime_error("model, feature-list and output directory required");
    Ort::Env env(ORT_LOGGING_LEVEL_WARNING, "endpoint-quant-trace");
    env.DisableTelemetryEvents();
    Ort::SessionOptions options;
    options.SetIntraOpNumThreads(1); options.SetInterOpNumThreads(1);
    options.SetExecutionMode(ORT_SEQUENTIAL);
    Ort::Session session(env, argv[1], options);
    if (session.GetInputCount() != 1 || session.GetOutputCount() > 100)
      throw std::runtime_error("oracle arity invalid");
    Ort::AllocatorWithDefaultOptions allocator;
    std::string input = session.GetInputNameAllocated(0, allocator).get();
    if (input != "input_features") throw std::runtime_error("input name changed");
    std::vector<std::string> names;
    std::vector<const char*> name_ptrs;
    for (size_t i = 0; i < session.GetOutputCount(); ++i)
      names.emplace_back(session.GetOutputNameAllocated(i, allocator).get());
    for (auto& name : names) name_ptrs.push_back(name.c_str());
    std::ifstream list(argv[2]); if (!list) throw std::runtime_error("feature list unreadable");
    std::string path; size_t case_id = 0;
    while (std::getline(list, path)) {
      if (++case_id > 8) throw std::runtime_error("oracle case bound exceeded");
      std::ifstream file(path, std::ios::binary);
      std::vector<float> x(64000);
      file.read(reinterpret_cast<char*>(x.data()), x.size()*sizeof(float));
      if (!file || file.peek() != std::char_traits<char>::eof()) throw std::runtime_error("feature extent differs");
      for (float v : x) if (!std::isfinite(v)) throw std::runtime_error("nonfinite features");
      const int64_t shape[] = {1,80,800};
      auto memory = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);
      auto tensor = Ort::Value::CreateTensor<float>(memory, x.data(), x.size(), shape, 3);
      const char* in[] = {input.c_str()};
      auto ys = session.Run(Ort::RunOptions{}, in, &tensor, 1, name_ptrs.data(), name_ptrs.size());
      for (size_t i = 0; i < ys.size(); ++i) {
        auto info = ys[i].GetTensorTypeAndShapeInfo();
        auto type = info.GetElementType();
        size_t width = type == ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT ? 4 :
                       (type == ONNX_TENSOR_ELEMENT_DATA_TYPE_UINT8 || type == ONNX_TENSOR_ELEMENT_DATA_TYPE_INT8 ? 1 : 0);
        if (!width || info.GetElementCount() > 1000000) throw std::runtime_error("output type/extent refused");
        size_t bytes = width*info.GetElementCount();
        std::string basename = std::to_string(case_id-1)+"."+std::to_string(i)+".bin";
        std::ofstream output(std::string(argv[3])+"/"+basename, std::ios::binary);
        output.write(static_cast<const char*>(ys[i].GetTensorRawData()), bytes);
        output.close(); if (!output) throw std::runtime_error("raw oracle write failed");
        std::cout << "{\"case\":" << case_id-1 << ",\"output\":" << i << ",\"name\":" << std::quoted(names[i])
                  << ",\"type\":" << int(type) << ",\"bytes\":" << bytes << ",\"file\":" << std::quoted(basename) << ",\"shape\":[";
        auto dims = info.GetShape(); for (size_t d=0;d<dims.size();++d) std::cout << (d ? "," : "") << dims[d];
        std::cout << "]}" << std::endl;
      }
    }
    if (case_id != 4) throw std::runtime_error("incomplete oracle panel");
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "endpoint quantization oracle failed: " << error.what() << '\n'; return 1;
  }
}
