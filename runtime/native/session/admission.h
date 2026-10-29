#pragma once
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <map>
#include <mutex>

namespace aii::voice {
constexpr double criterion_rtf = 0.8;
constexpr double criterion_first_sound_ms = 2000;

struct LaneCosts {
  double rtf = 0;
  double first_sound_ms = 0;
  bool measured = false;
};

class Admission {
 public:
  struct Answer {
    bool admitted = false;
    uint32_t retry_after_ms = 0;
    uint32_t held = 0;
  };
  Admission(size_t ceiling, std::chrono::milliseconds longest, std::function<LaneCosts()> costs);
  Answer admit(uint64_t holder);
  void release(uint64_t holder);
  size_t held() const;

 private:
  const size_t ceiling_;
  const std::chrono::milliseconds longest_;
  const std::function<LaneCosts()> costs_;
  mutable std::mutex mutex_;
  std::map<uint64_t, std::chrono::steady_clock::time_point> held_;
  double mean_session_ms_ = 0;
  bool have_mean_ = false;
};
}
