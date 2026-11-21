#include "admission.h"
#include <algorithm>

namespace aii::voice {
Admission::Admission(size_t ceiling, std::chrono::milliseconds longest, std::function<LaneCosts()> costs)
    : ceiling_(ceiling), longest_(longest), costs_(std::move(costs)) {}

Admission::Answer Admission::admit(uint64_t holder) {
  const auto costs = costs_ ? costs_() : LaneCosts{};
  std::lock_guard<std::mutex> lock(mutex_);
  if (held_.count(holder)) return {true, 0, uint32_t(held_.size())};
  const double with_one_more = double(held_.size() + 1);
  const bool fits = held_.size() + 1 <= ceiling_ &&
                    (held_.empty() || !costs.measured ||
                     (with_one_more * costs.rtf <= criterion_rtf &&
                      with_one_more * costs.first_sound_ms <= criterion_first_sound_ms));
  if (fits) {
    held_[holder] = std::chrono::steady_clock::now();
    return {true, 0, uint32_t(held_.size())};
  }
  const auto now = std::chrono::steady_clock::now();
  auto oldest = now;
  for (const auto& [id, since] : held_) oldest = std::min(oldest, since);
  const double ran = std::chrono::duration<double, std::milli>(now - oldest).count();
  const double longest = double(longest_.count());
  const double estimate = have_mean_ ? std::clamp(mean_session_ms_ - ran, 1.0, longest) : longest;
  return {false, uint32_t(estimate), uint32_t(held_.size())};
}

void Admission::release(uint64_t holder) {
  std::lock_guard<std::mutex> lock(mutex_);
  const auto found = held_.find(holder);
  if (found == held_.end()) return;
  const double ran = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - found->second).count();
  mean_session_ms_ = have_mean_ ? 0.8 * mean_session_ms_ + 0.2 * ran : ran;
  have_mean_ = true;
  held_.erase(found);
}

size_t Admission::held() const {
  std::lock_guard<std::mutex> lock(mutex_);
  return held_.size();
}
}
