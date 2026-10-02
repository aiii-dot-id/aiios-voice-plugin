#include "worker_environment.h"
#include <string>

int main() {
  for (bool vulkan_tts : {false, true, false}) {
#ifdef _WIN32
    if (_putenv_s("GGML_VK_DISABLE_F16", "0")) return 1;
#else
    if (setenv("GGML_VK_DISABLE_F16", "0", 1)) return 1;
#endif
    aii::voice::configure_worker_environment(vulkan_tts);
    for (const char* key : {"GGML_VK_DISABLE_F16", "ORT_DISABLE_TELEMETRY"}) {
      const char* value = std::getenv(key);
      if (!value || std::string(value) != "1") return 2;
    }
  }
  return 0;
}
