#include "registered_accelerators.h"
#include <cstdio>
#include <string>

int main() {
  int failed = 0;
  const auto expect = [&](const char* set, const std::string& got, const char* want) {
    if (got != want) { std::fprintf(stderr, "%s: got %s, want %s\n", set, got.c_str(), want); ++failed; }
  };
  const std::string cpu_asr = R"({"encoder_provider":"CPUExecutionProvider","diarization_provider":"CPUExecutionProvider"})";
  const std::string cuda_asr = R"({"encoder_provider":"CUDAExecutionProvider","diarization_provider":"NeMo-Speech.cpp"})";
  const std::string dml_asr = R"({"encoder_provider":"DmlExecutionProvider","decoder_provider":"CPUExecutionProvider"})";
  const std::string coreml_asr = R"({"encoder_provider":"CoreMLExecutionProvider","decoder_provider":"CoreMLExecutionProvider"})";
  using aii::voice::registered_accelerators;
  expect("linux small-cpu", registered_accelerators("cpu", cpu_asr, "cpu"), "cpu");
  expect("linux small", registered_accelerators("vulkan", cpu_asr, "cpu"), "cpu+vulkan");
  expect("linux full", registered_accelerators("vulkan", cuda_asr, "cuda"), "cpu+cuda+vulkan");
  expect("windows full", registered_accelerators("vulkan", dml_asr, "cpu"), "cpu+directml+vulkan");
  expect("macos small", registered_accelerators("metal", cpu_asr, "cpu"), "cpu+metal");
  expect("macos full", registered_accelerators("metal", coreml_asr, "coreml-ane"), "cpu+metal+coreml");
  expect("uid on vulkan alone", registered_accelerators("cpu", cpu_asr, "ncnn-vulkan"), "cpu+vulkan");
  expect("no word twice", registered_accelerators("vulkan", cuda_asr + cuda_asr, "ncnn-vulkan"), "cpu+cuda+vulkan");

  char field[32]{};
  aii::voice::put_accelerator(field, std::string("cpu+cuda+vulkan"));
  expect("copied whole", field, "cpu+cuda+vulkan");
  bool refused = false;
  try { aii::voice::put_accelerator(field, std::string(32, 'x')); } catch (const std::runtime_error&) { refused = true; }
  if (!refused) { std::fprintf(stderr, "a line longer than the field was cut, not refused\n"); ++failed; }
  return failed;
}
