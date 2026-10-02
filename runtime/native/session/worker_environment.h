#pragma once
#include <cstdlib>
#include <stdexcept>

namespace aii::voice {
// Apply before loading any model. Diarization may use Vulkan even when TTS
// uses CPU; its validated precision must not depend on the TTS placement.
inline void configure_worker_environment(bool vulkan_tts) {
#ifdef _WIN32
  if (_putenv_s("ORT_DISABLE_TELEMETRY", "1") ||
      _putenv_s("GGML_VK_DISABLE_F16", "1"))
    throw std::runtime_error("cannot bind native telemetry/precision policy");
  if (vulkan_tts && _putenv_s("GGML_VK_VISIBLE_DEVICES", "0"))
    throw std::runtime_error("cannot bind explicit Vulkan device");
#else
  if (setenv("ORT_DISABLE_TELEMETRY", "1", 1) ||
      setenv("GGML_VK_DISABLE_F16", "1", 1))
    throw std::runtime_error("cannot bind native telemetry/precision policy");
  if (vulkan_tts &&
#if defined(__linux__) && !defined(__ANDROID__)
      unsetenv("GGML_VK_VISIBLE_DEVICES")
#else
      setenv("GGML_VK_VISIBLE_DEVICES", "0", 1)
#endif
      )
    throw std::runtime_error("cannot bind explicit Vulkan device");
#endif
}
} // namespace aii::voice
