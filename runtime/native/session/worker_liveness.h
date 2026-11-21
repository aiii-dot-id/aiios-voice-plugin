#pragma once

#ifndef _WIN32
#include <cerrno>
#include <cstdlib>
#include <stdexcept>
#include <string>
#include <fcntl.h>
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
inline int inherited_descriptor(const char* variable) {
  const char* value = std::getenv(variable);
  if (!value) throw std::runtime_error(std::string(variable) + " required");
  char* end = nullptr;
  const long parsed = std::strtol(value, &end, 10);
  if (end == value || *end || parsed < 3 || parsed > 1024)
    throw std::runtime_error(std::string("invalid ") + variable);
  const int fd = int(parsed), flags = ::fcntl(fd, F_GETFD);
  if (flags < 0) throw std::runtime_error(std::string(variable) + " is not open");
  (void)::fcntl(fd, F_SETFD, flags | FD_CLOEXEC);
  return fd;
}
}
#else
namespace aii::voice {
inline void watch_carrier_liveness() {}
}
#endif
#include <cstdint>
#include <utility>
namespace aii::voice {
std::pair<intptr_t, intptr_t> inherited_ends(const char* descriptor, const char* in, const char* out);
}
