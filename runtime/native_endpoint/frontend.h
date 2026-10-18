#pragma once
#include <cstddef>
#include <array>
#include <functional>
#include <vector>

namespace aii::endpoint {
class Frontend {
 public:
  explicit Frontend(const float* coefficients, size_t count);
  std::vector<float> features(const float* samples, size_t count,
                             const std::function<bool()>& cancelled) const;
 private:
  std::vector<float> coefficients_;
#ifdef AII_ENDPOINT_SPARSE_FRONTEND
  std::array<std::vector<size_t>, 80> nonzero_bins_;
#endif
};
}
