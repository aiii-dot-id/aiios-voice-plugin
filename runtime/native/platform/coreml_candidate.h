#pragma once
#include "onnxruntime_cxx_api.h"
#ifdef AII_MOBILE_COREML_CANDIDATE
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <stdexcept>
#include <string>
#endif

namespace aii::platform {
// Explicit app-build candidate, not a silent replacement for desktop CPU.
// A provider request is not hardware-placement evidence: retain ORT profiles
// and Core ML compute plans separately, including unsupported CPU partitions.
inline void coreml_candidate(Ort::SessionOptions& options, const char* component) {
#ifdef AII_MOBILE_COREML_CANDIDATE
  const auto providers=Ort::GetAvailableProviders();
  if(std::find(providers.begin(),providers.end(),"CoreMLExecutionProvider")==providers.end())
    throw std::runtime_error("requested Core ML provider is absent; no provider substitution");
  const char* directory=std::getenv("AII_MOBILE_PROFILE_DIR");
  if(!directory || !*directory)throw std::runtime_error("Core ML candidate needs a profile directory");
  options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_DISABLE_ALL);
  options.SetLogSeverityLevel(0);
  options.EnableProfiling((std::string(directory)+"/ort-"+component).c_str());
  options.AppendExecutionProvider("CoreML",{
    {"MLComputeUnits","CPUAndNeuralEngine"}, {"ModelFormat","MLProgram"},
    {"RequireStaticInputShapes","0"}, {"EnableOnSubgraphs","0"},
    // Compute-plan diagnostics failed to return during the first on-device
    // initialization. Keep ORT execution profiling; collect hardware traces
    // separately instead of making readiness depend on optional diagnostics.
    {"ProfileComputePlan","0"}});
  std::fprintf(stderr,"{\"kind\":\"provider_requested\",\"component\":\"%s\","
      "\"provider\":\"CoreMLExecutionProvider\",\"compute_units\":\"CPUAndNeuralEngine\","
      "\"hardware_execution_verified\":false}\n",component);
#else
  (void)options;(void)component;
#endif
}
}
