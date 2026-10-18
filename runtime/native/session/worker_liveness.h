#pragma once

#ifndef _WIN32
#include <cerrno>
#include <cstdlib>
#include <stdexcept>
#include <thread>
#include <unistd.h>

namespace aii::voice {
inline void watch_carrier_liveness() {
  const char* value = std::getenv("AII_VOICE_CARRIER_LIVENESS_FD");
  if (!value) return;
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
      std::_Exit(count == 0 ? 74 : 75);
    }
  }).detach();
}
}
#else
namespace aii::voice {
inline void watch_carrier_liveness() {}
}
#endif
