#pragma once
#include "onnxruntime_c_api.h"
#include <cstdio>
#include <cstring>

namespace aii::asr {
// The sealed encoder contains these two harmless, unused constants. Keep the
// signed model bytes and every other diagnostic; never filter child stderr or
// turn all warning/error visibility off just to remove these startup notices.
inline bool unused_encoder_notice(OrtLoggingLevel severity, const char* where,
                                  const char* message) noexcept {
  if (severity != ORT_LOGGING_LEVEL_WARNING || !where || !message ||
      !std::strstr(where, "CleanUnusedInitializersAndNodeArgs")) return false;
  return !std::strcmp(message, "Removing initializer '/Constant_4_output_0'. It is not used by any node and should be removed from the model.") ||
         !std::strcmp(message, "Removing initializer '/Constant_7_output_0'. It is not used by any node and should be removed from the model.");
}
inline void ORT_API_CALL onnx_log(void* destination, OrtLoggingLevel severity,
    const char* category, const char* logid, const char* where, const char* message) noexcept {
  if (unused_encoder_notice(severity, where, message)) return;
  const char* levels[] = {"verbose", "info", "warning", "error", "fatal"};
  const int level = static_cast<int>(severity);
  // One stdio call retains line serialization across ORT's worker threads.
  std::fprintf(destination ? static_cast<FILE*>(destination) : stderr,
      "[onnxruntime %s %s %s %s] %s\n", level >= 0 && level < 5 ? levels[level] : "unknown",
      category ? category : "", logid ? logid : "", where ? where : "", message ? message : "");
}
}
