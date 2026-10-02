#pragma once
#include "onnxruntime_c_api.h"
#include <cstring>

namespace uid_detail {
// Pinned ORT termination diagnostic. Any other error, including one racing a
// cancel request, remains a fault. A future runtime must re-prove this mapping;
// an unknown message fails closed rather than granting model reuse.
inline bool run_was_cancelled(bool requested, OrtErrorCode code, const char* message) {
  return requested && code == ORT_FAIL && message &&
      std::strcmp(message, "Exiting due to terminate flag being set to true.") == 0;
}
}
