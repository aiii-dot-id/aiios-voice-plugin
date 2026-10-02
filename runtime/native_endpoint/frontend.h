#pragma once
#include <cstddef>
#include <array>
#include <functional>
#include <vector>

namespace aii::endpoint {
class Frontend {
 public:
  // Bound reference mel [201,80], followed by the reference periodic Hann[400].
  explicit Frontend(const float* coefficients, size_t count);
  std::vector<float> features(const float* samples, size_t count,
                             const std::function<bool()>& cancelled) const;
 private:
  std::vector<float> coefficients_;
#ifdef AII_ENDPOINT_SPARSE_FRONTEND
  // Indices retain the dense band's bin order; coefficients are never rounded.
  std::array<std::vector<size_t>, 80> nonzero_bins_;
#endif
};
}
