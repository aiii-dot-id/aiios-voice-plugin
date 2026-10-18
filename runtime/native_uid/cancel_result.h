#pragma once
#include "onnxruntime_c_api.h"
#include <cstring>

namespace uid_detail {
inline bool run_was_cancelled(bool requested, OrtErrorCode code, const char* message) {
  return requested && code == ORT_FAIL && message &&
      std::strcmp(message, "Exiting due to terminate flag being set to true.") == 0;
}
}
