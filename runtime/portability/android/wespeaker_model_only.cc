// Physical-Android model-only conformance runner for the sealed WeSpeaker vectors.
//
// This intentionally accepts precomputed fbank tensors. Audio decoding and feature
// extraction belong to the separate end-to-end portability gate.

#include <onnxruntime_cxx_api.h>

#include <array>
#include <chrono>
#include <cmath>
#include <cstring>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

struct NpyFloat32 {
  std::vector<int64_t> shape;
  std::vector<float> values;
};

std::vector<uint8_t> ReadBytes(const std::string& path) {
  std::ifstream input(path, std::ios::binary | std::ios::ate);
  if (!input) throw std::runtime_error("cannot open input: " + path);
  const auto end = input.tellg();
  if (end < 0) throw std::runtime_error("cannot determine input size: " + path);
  std::vector<uint8_t> bytes(static_cast<size_t>(end));
  input.seekg(0);
  input.read(reinterpret_cast<char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
  if (!input) throw std::runtime_error("cannot read complete input: " + path);
  return bytes;
}

std::vector<int64_t> ParseShape(const std::string& header) {
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
    while (cursor < close && (header[cursor] == ' ' || header[cursor] == ',')) ++cursor;
    if (cursor >= close) break;
    if (header[cursor] < '0' || header[cursor] > '9') {
      throw std::runtime_error("npy shape contains a non-positive-integer token");
    }
    int64_t value = 0;
    while (cursor < close && header[cursor] >= '0' && header[cursor] <= '9') {
      value = value * 10 + (header[cursor] - '0');
      ++cursor;
    }
    if (value <= 0) throw std::runtime_error("npy shape dimensions must be positive");
    shape.push_back(value);
  }
  if (shape.empty()) throw std::runtime_error("npy shape is empty");
  return shape;
}

NpyFloat32 ReadNpyFloat32(const std::string& path) {
  const auto bytes = ReadBytes(path);
  if (bytes.size() < 10 || bytes[0] != 0x93 || bytes[1] != 'N' || bytes[2] != 'U' ||
      bytes[3] != 'M' || bytes[4] != 'P' || bytes[5] != 'Y') {
    throw std::runtime_error("input is not an npy file");
  }
  const uint8_t major = bytes[6];
  size_t header_offset = 0;
  size_t header_size = 0;
  if (major == 1) {
    header_offset = 10;
    header_size = static_cast<size_t>(bytes[8]) |
                  (static_cast<size_t>(bytes[9]) << 8);
  } else if (major == 2 || major == 3) {
    if (bytes.size() < 12) throw std::runtime_error("truncated npy v2/v3 header");
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
  const std::string header(reinterpret_cast<const char*>(bytes.data() + header_offset),
                           header_size);
  if (header.find("<f4") == std::string::npos && header.find("|f4") == std::string::npos) {
    throw std::runtime_error("npy tensor must be little-endian float32");
  }
  if (header.find("fortran_order': True") != std::string::npos ||
      header.find("fortran_order\": true") != std::string::npos) {
    throw std::runtime_error("Fortran-order npy tensors are unsupported");
  }
  auto shape = ParseShape(header);
  size_t count = 1;
  for (const auto dimension : shape) count *= static_cast<size_t>(dimension);
  const size_t data_offset = header_offset + header_size;
  if (bytes.size() != data_offset + count * sizeof(float)) {
    throw std::runtime_error("npy payload size differs from shape");
  }
  std::vector<float> values(count);
  std::memcpy(values.data(), bytes.data() + data_offset, count * sizeof(float));
  return {std::move(shape), std::move(values)};
}

void WriteFloat32(const std::string& path, const std::vector<float>& values) {
  std::ofstream output(path, std::ios::binary | std::ios::trunc);
  if (!output) throw std::runtime_error("cannot open output: " + path);
  output.write(reinterpret_cast<const char*>(values.data()),
               static_cast<std::streamsize>(values.size() * sizeof(float)));
  if (!output) throw std::runtime_error("cannot write complete output: " + path);
}

}  // namespace

int main(int argc, char** argv) {
  try {
    if (argc != 4) {
      std::cerr << "usage: wespeaker_model_only MODEL FEATURE_NPY OUTPUT_F32\n";
      return 2;
    }
    const auto feature = ReadNpyFloat32(argv[2]);
    if (feature.shape.size() != 2 || feature.shape[1] != 80) {
      throw std::runtime_error("feature tensor must have shape [frames, 80]");
    }

    Ort::Env environment(ORT_LOGGING_LEVEL_WARNING, "aii-voice-portability");
    Ort::SessionOptions options;
    options.SetIntraOpNumThreads(1);
    options.SetInterOpNumThreads(1);
    options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_ALL);
    Ort::Session session(environment, argv[1], options);

    auto memory = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);
    const std::array<int64_t, 3> input_shape = {1, feature.shape[0], 80};
    auto input = Ort::Value::CreateTensor<float>(
        memory, const_cast<float*>(feature.values.data()), feature.values.size(),
        input_shape.data(), input_shape.size());
    const char* input_names[] = {"feats"};
    const char* output_names[] = {"embs"};

    const auto start = std::chrono::steady_clock::now();
    auto outputs = session.Run(Ort::RunOptions{nullptr}, input_names, &input, 1,
                               output_names, 1);
    const auto stop = std::chrono::steady_clock::now();
    if (outputs.size() != 1 || !outputs[0].IsTensor()) {
      throw std::runtime_error("runtime did not emit one embedding tensor");
    }
    const auto output_shape = outputs[0].GetTensorTypeAndShapeInfo().GetShape();
    if (output_shape.size() != 2 || output_shape[0] != 1 || output_shape[1] != 256) {
      throw std::runtime_error("embedding tensor must have shape [1, 256]");
    }
    const float* raw = outputs[0].GetTensorData<float>();
    std::vector<float> embedding(raw, raw + 256);
    double squared_norm = 0.0;
    for (const auto value : embedding) {
      if (!std::isfinite(value)) throw std::runtime_error("embedding contains nonfinite value");
      squared_norm += static_cast<double>(value) * value;
    }
    if (!(squared_norm > 0.0)) throw std::runtime_error("embedding has zero norm");
    const float inverse_norm = static_cast<float>(1.0 / std::sqrt(squared_norm));
    for (auto& value : embedding) value *= inverse_norm;
    WriteFloat32(argv[3], embedding);

    const auto elapsed =
        std::chrono::duration_cast<std::chrono::nanoseconds>(stop - start).count();
    std::cout << "{\"frames\":" << feature.shape[0]
              << ",\"embedding_dim\":256,\"inference_ns\":" << elapsed << "}\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "wespeaker_model_only: " << error.what() << "\n";
    return 1;
  }
}
