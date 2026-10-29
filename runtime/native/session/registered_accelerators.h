#pragma once
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

namespace aii::voice {
inline std::string registered_accelerators(std::string_view tts_backend, std::string_view recognizer_execution,
                                           std::string_view uid_backend_name) {
  const auto named = [&](std::string_view token) { return recognizer_execution.find(token) != std::string_view::npos; };
  bool cuda = named("CUDAExecutionProvider"), directml = named("DmlExecutionProvider") || named("\"directml\""),
       coreml = named("CoreMLExecutionProvider"), vulkan = tts_backend == "vulkan", metal = tts_backend == "metal";
  if (uid_backend_name == "cuda") cuda = true;
  else if (uid_backend_name == "coreml-ane") coreml = true;
  else if (uid_backend_name == "ncnn-vulkan") vulkan = true;
  std::string out = "cpu";
  for (const auto& [on, word] : {std::pair{cuda, "cuda"}, std::pair{directml, "directml"}, std::pair{vulkan, "vulkan"},
                                 std::pair{metal, "metal"}, std::pair{coreml, "coreml"}})
    if (on) { out += '+'; out += word; }
  return out;
}

template <size_t N> void put_accelerator(char (&field)[N], const std::string& value) {
  if (value.size() >= N) throw std::runtime_error("registered accelerators do not fit the readiness field: " + value);
  value.copy(field, value.size());
  field[value.size()] = 0;
}
}
