#include "onnx_log.h"
#include <memory>
#include <stdexcept>
#include <string>
#include <iostream>
int main() { try {
  std::unique_ptr<FILE, decltype(&std::fclose)> file(std::tmpfile(), std::fclose);
  if (!file) throw std::runtime_error("temporary diagnostic sink unavailable");
  const char* location = "graph.cc:5241 CleanUnusedInitializersAndNodeArgs";
  for (const auto* name : {"/Constant_4_output_0", "/Constant_7_output_0"}) {
    const std::string message = std::string("Removing initializer '") + name +
        "'. It is not used by any node and should be removed from the model.";
    aii::asr::onnx_log(file.get(), ORT_LOGGING_LEVEL_WARNING, "graph", "test", location, message.c_str());
    if (std::ftell(file.get()) != 0) throw std::runtime_error("known unused encoder notice escaped");
  }
  aii::asr::onnx_log(file.get(), ORT_LOGGING_LEVEL_WARNING, "provider", "test", "provider.cc", "accelerator fallback warning");
  aii::asr::onnx_log(file.get(), ORT_LOGGING_LEVEL_WARNING, "graph", "test", location, "unexpected unused initializer");
  aii::asr::onnx_log(file.get(), ORT_LOGGING_LEVEL_ERROR, "graph", "test", location,
      "Removing initializer '/Constant_4_output_0'. It is not used by any node and should be removed from the model.");
  aii::asr::onnx_log(file.get(), ORT_LOGGING_LEVEL_FATAL, nullptr, nullptr, nullptr, "real fatal failure");
  std::rewind(file.get());
  char buffer[2048]{};
  const std::string result(buffer, std::fread(buffer, 1, sizeof(buffer), file.get()));
  for (const char* required : {"accelerator fallback warning", "unexpected unused initializer",
       "[onnxruntime error", "'/Constant_4_output_0'", "[onnxruntime fatal", "real fatal failure"})
    if (result.find(required) == std::string::npos) throw std::runtime_error(std::string("diagnostic swallowed: ") + required);
  std::cout << "PASS: only the two exact warning notices suppressed; other warnings, errors and fatal diagnostics retained\n";
} catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; } }
