#pragma once

#ifndef _WIN32
#include <cerrno>
#include <cstdlib>
#include <stdexcept>
#include <thread>
#include <unistd.h>

namespace aii::voice {
// Private carrier-owned descriptor, not a public SDK or audio protocol.
// Start before model loading: stdin is not read until after warm inference.
inline void watch_carrier_liveness() {
  const char* value = std::getenv("AII_VOICE_CARRIER_LIVENESS_FD");
  if (!value) return; // Direct development probes do not run under a carrier.
  char* end = nullptr;
  const long parsed = std::strtol(value, &end, 10);
  if (end == value || *end || parsed < 3 || parsed > 1024)
    throw std::runtime_error("invalid carrier liveness descriptor");
  const int fd = int(parsed);
  std::thread([fd] {
    char byte;
    for (;;) {
      const auto count = ::read(fd, &byte, 1);
      if (count < 0 && errno == EINTR) continue;
      // No bytes are ever sent. EOF means the owning carrier is gone;
      // unexpected bytes or read errors are also fail-closed.
      std::_Exit(count == 0 ? 74 : 75);
    }
  }).detach();
}
} // namespace aii::voice
#else
namespace aii::voice {
inline void watch_carrier_liveness() {} // Windows owns the process tree with a Job.
}
#endif
