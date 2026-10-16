#pragma once
#include <initializer_list>
#include <stdexcept>

namespace aii::pocket {
// A deterministic placement policy, not a claim of benchmarking every GPU.
// Registry indices are NOT physical Vulkan enumeration indices. Software/CPU
// adapters are never substituted for requested hardware inference.
template<class Devices>
typename Devices::value_type prefer_vulkan_hardware(const Devices& devices) {
  for(const char* kind : {"GPU", "IGPU"}) {
    for(const auto& device : devices) {
      if(device.backend != "Vulkan" || device.type != kind) continue;
      if(device.index < 0 || device.name.empty())
        throw std::runtime_error("Vulkan device identity unavailable");
      return device;
    }
  }
  throw std::runtime_error("Vulkan hardware GPU unavailable; CPU/software fallback refused");
}
}
