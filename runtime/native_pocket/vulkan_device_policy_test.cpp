#include "vulkan_device_policy.h"
#include <iostream>
#include <string>
#include <vector>

struct Device { std::string backend; int index; std::string name, type; };
using Devices = std::vector<Device>;

int main() {
  int failures = 0;
  auto select = [&](const char* name, const Devices& devices, int expected) {
    try {
      const auto actual = aii::pocket::prefer_vulkan_hardware(devices);
      if (actual.index != expected) {
        std::cerr << name << ": selected registry index " << actual.index
                  << ", expected " << expected << '\n'; ++failures;
      }
    } catch (const std::exception& e) {
      std::cerr << name << ": unexpected refusal: " << e.what() << '\n'; ++failures;
    }
  };
  auto refuse = [&](const char* name, const Devices& devices) {
    try {
      const auto actual = aii::pocket::prefer_vulkan_hardware(devices);
      std::cerr << name << ": incorrectly admitted " << actual.name << '\n'; ++failures;
    } catch (const std::runtime_error&) {}
  };
  const Device integrated{"Vulkan", 0, "Integrated", "IGPU"};
  const Device discrete{"Vulkan", 7, "Discrete", "GPU"};
  const Device software{"Vulkan", 1, "Software", "CPU"};
  select("integrated-first must prefer discrete", {integrated, discrete, software}, 7);
  select("discrete-first preserves registry index", {discrete, integrated}, 7);
  select("integrated-only stays hardware", {software, integrated}, 0);
  select("other registries cannot hijack selection", {{"CUDA", 3, "Other", "GPU"}, integrated}, 0);
  select("multiple discrete uses stable first registry entry", {discrete, {"Vulkan", 9, "Second", "GPU"}}, 7);
  refuse("software is not a GPU fallback", {software});
  refuse("empty inventory", {});
  refuse("unknown device kind", {{"Vulkan", 2, "Unknown", "META"}});
  refuse("negative registry index", {{"Vulkan", -1, "Bad", "GPU"}});
  refuse("missing device identity", {{"Vulkan", 0, "", "GPU"}});
  if (!failures) std::cout << "10 Vulkan placement cases passed\n";
  return failures ? 1 : 0;
}
