#pragma once
#include "onnxruntime_cxx_api.h"
#if defined(AII_MOBILE_COREML_CANDIDATE) || defined(AII_UID_COREML)
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <filesystem>
#endif

namespace aii::platform {
#if defined(AII_MOBILE_COREML_CANDIDATE) || defined(AII_UID_COREML)
// Same provider configuration for production and qualification. Profiling is
// an explicit diagnostic-build concern, never a prerequisite for inference.
inline void coreml_provider(Ort::SessionOptions& options, const char* format,
                            const char* cache=nullptr) {
  const auto providers=Ort::GetAvailableProviders();
  if(std::find(providers.begin(),providers.end(),"CoreMLExecutionProvider")==providers.end())
    throw std::runtime_error("requested Core ML provider is absent; no provider substitution");
  options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_DISABLE_ALL);
  std::unordered_map<std::string,std::string> provider_options={
    {"MLComputeUnits","CPUAndNeuralEngine"}, {"ModelFormat",format},
    {"RequireStaticInputShapes","0"}, {"EnableOnSubgraphs","0"},
    {"ProfileComputePlan","0"}};
  if(cache && *cache) {
    if(!std::filesystem::is_directory(cache))
      throw std::runtime_error("bound Core ML cache directory is unavailable");
    provider_options.emplace("ModelCacheDirectory",cache);
  }
  options.AppendExecutionProvider("CoreML",provider_options);
}
#endif
// Explicit app-build candidate, not a silent replacement for desktop CPU.
// A provider request is not hardware-placement evidence: retain ORT profiles
// and Core ML compute plans separately, including unsupported CPU partitions.
inline void coreml_candidate(Ort::SessionOptions& options, const char* component,
                             const char* format="MLProgram") {
#ifdef AII_MOBILE_COREML_CANDIDATE
  const char* directory=std::getenv("AII_MOBILE_PROFILE_DIR");
  if(!directory || !*directory)throw std::runtime_error("Core ML candidate needs a profile directory");
  options.SetLogSeverityLevel(0);
  options.EnableProfiling((std::string(directory)+"/ort-"+component).c_str());
  coreml_provider(options,format);
  std::fprintf(stderr,"{\"kind\":\"provider_requested\",\"component\":\"%s\","
      "\"provider\":\"CoreMLExecutionProvider\",\"compute_units\":\"CPUAndNeuralEngine\","
      "\"model_format\":\"%s\",\"hardware_execution_verified\":false}\n",component,format);
#else
  (void)options;(void)component;(void)format;
#endif
}
}
