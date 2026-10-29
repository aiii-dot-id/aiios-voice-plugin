#include "failure_codes.h"
#include <cstdio>
#include <string>

int main() {
  int failed = 0;
  const auto expect = [&](const char* reason, const char* code) {
    const auto got = aii::voice::momentary_failure(reason);
    const std::string have = got ? got->code : "";
    if (have != (code ? code : "")) { std::fprintf(stderr, "%s: got '%s'\n", reason, have.c_str()); ++failed; }
  };
  expect("recognition model call exceeded progress deadline: 30000 ms, the time the limits table gives it (model_call_ms)", "MODEL_DEADLINE");
  expect("speaker model call exceeded progress deadline: 30000 ms", "MODEL_DEADLINE");
  expect("native pipe write expired: 3000 ms, the time the limits table gives it (audio_write_ms)", "AUDIO_WRITE_DEADLINE");
  expect("host settings: no answer from the host in time", "HOST_SETTINGS_NO_ANSWER");
  expect("Nemotron: ggml_vulkan: Device memory allocation of size 268435456 failed. vk::Device::allocateMemory: ErrorOutOfDeviceMemory", "DEVICE_MEMORY");
  expect("[ONNXRuntimeError] : 6 : RUNTIME_EXCEPTION : CUDA failure 2: out of memory", "DEVICE_MEMORY");
  expect("std::bad_alloc", "DEVICE_MEMORY");
  expect("ggml_backend_alloc_ctx_tensors: failed to allocate buffer", "DEVICE_MEMORY");
  expect("model server lost", "MODEL_SERVER_LOST");
  expect("Nemotron: model server lost", "MODEL_SERVER_LOST");
  expect("engine busy: 5 sessions held, its measured costs keep no more within the criterion", "ENGINE_BUSY");
  expect("host settings: the host refused the read", nullptr);
  expect("unsupported speaking language", nullptr);
  expect("stale session", nullptr);
  expect("native backend 1: invalid voice preset", nullptr);
  return failed;
}
