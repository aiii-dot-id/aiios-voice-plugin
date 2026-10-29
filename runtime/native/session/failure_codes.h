#pragma once
#include <algorithm>
#include <cctype>
#include <optional>
#include <string>
#include <string_view>

namespace aii::voice {
struct FailureCode { const char* code; };
inline std::optional<FailureCode> momentary_failure(std::string_view reason) {
  std::string lower(reason);
  std::transform(lower.begin(), lower.end(), lower.begin(), [](unsigned char c) { return char(std::tolower(c)); });
  const auto has = [&](const char* words) { return lower.find(words) != std::string::npos; };
  if (has("model call exceeded progress deadline")) return FailureCode{"MODEL_DEADLINE"};
  if (has("native pipe write expired")) return FailureCode{"AUDIO_WRITE_DEADLINE"};
  if (has("host settings: no answer from the host in time")) return FailureCode{"HOST_SETTINGS_NO_ANSWER"};
  if (has("model server lost")) return FailureCode{"MODEL_SERVER_LOST"};
  if (has("engine busy")) return FailureCode{"ENGINE_BUSY"};
  for (const char* words : {"out of memory", "outofdevicememory", "outofhostmemory", "failed to allocate",
                            "memoryallocation", "bad_alloc", "insufficient memory"})
    if (has(words)) return FailureCode{"DEVICE_MEMORY"};
  return std::nullopt;
}
}
