#include "diar_cache.h"
#include <cmath>
#include <cstdio>
#include <limits>
#include <stdexcept>
#include <vector>

namespace {
using aii::multitalker::DiarCache;
std::vector<float> chunk(size_t frames, float value) {
  return std::vector<float>(frames * DiarCache::width, value);
}
std::vector<float> probabilities(size_t frames, float value) {
  return std::vector<float>(frames * DiarCache::speakers, value);
}
void expect(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}
}

int main() {
  try {
    DiarCache cache;
    auto first = chunk(2, 1.f);
    auto prediction = probabilities(2, 0.f);
    expect(cache.input(first).size() == first.size(), "initial input geometry");
    expect(cache.update(first, prediction).size() == prediction.size(),
           "initial output geometry");
    expect(cache.frames() == 2, "initial frame count");

    // Force FIFO rollover and compression with silence-only predictions. The
    // cache remains bounded and uses the learned silence vector for padding.
    for (size_t i = 0; i < 12; ++i) {
      auto part = chunk(32, static_cast<float>(i + 2));
      auto p = probabilities(cache.frames() + 32, 0.f);
      cache.update(part, p);
    }
    expect(cache.frames() <= DiarCache::cache_limit + DiarCache::fifo_limit,
           "cache bound");
    expect(cache.compressions() > 0, "compression occurred");

    bool rejected = false;
    try { cache.update(std::vector<float>(3, 1.f), {}); }
    catch (const std::invalid_argument&) { rejected = true; }
    expect(rejected, "misaligned chunk accepted");
    rejected = false;
    auto bad = probabilities(1, 0.f);
    bad[0] = std::numeric_limits<float>::quiet_NaN();
    try { cache.update(chunk(1, 1.f), bad); }
    catch (const std::invalid_argument&) { rejected = true; }
    expect(rejected, "nonfinite prediction accepted");
    return 0;
  } catch (const std::exception& error) {
    std::fprintf(stderr, "%s\n", error.what());
    return 1;
  }
}
