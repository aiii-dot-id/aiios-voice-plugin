#include "frontend.h"
#include "pocketfft_hdronly.h"
#include "tables.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <complex>
#include <limits>
#include <vector>

extern "C" int aii_ecapa_fbank(const uint8_t *pcm, size_t bytes, int rate,
                               float *output, size_t capacity, size_t *frames,
                               aii_ecapa_cancelled cancelled, void *context) {
  if (!frames)
    return 1;
  *frames = 0;
  if (!pcm || !output || rate != 16000 || bytes == 0 || bytes % 2 ||
      bytes > 960000)
    return 1;
  const size_t count = bytes / 2, n = 1 + count / 160;
  if (capacity < n * 80)
    return 1;
  auto stopped = [&] { return cancelled && cancelled(context); };
  try {
    if (stopped())
      return 3;
    std::vector<float> result(n * 80);
    std::array<float, 400> windowed{};
    std::array<std::complex<float>, 201> spectrum{};
    std::array<float, 201> power{};
    float peak = -std::numeric_limits<float>::infinity();
    for (size_t t = 0; t < n; ++t) {
      if (stopped())
        return 3;
      for (size_t j = 0; j < 400; ++j) {
        const auto i = static_cast<int64_t>(t * 160 + j) - 200;
        float value = 0;
        if (i >= 0 && static_cast<size_t>(i) < count) {
          const auto k = static_cast<size_t>(i) * 2;
          int sample = int(pcm[k]) | (int(pcm[k + 1]) << 8);
          if (sample >= 32768)
            sample -= 65536;
          value = static_cast<float>(sample) / 32768.0f;
        }
        windowed[j] = value * aii::ecapa::window[j];
      }
      pocketfft::r2c<float>({400}, {sizeof(float)},
                            {sizeof(std::complex<float>)}, 0, true,
                            windowed.data(), spectrum.data(), 1.0f, 1);
      for (size_t k = 0; k < 201; ++k)
        power[k] = spectrum[k].real() * spectrum[k].real() +
                   spectrum[k].imag() * spectrum[k].imag();
      for (size_t m = 0; m < 80; ++m) {
        float energy = 0;
        const auto &band = aii::ecapa::bands[m];
        // Skip exact zero coefficients only; retain the reference summation
        // order.
        for (size_t k = 0; k < band.count; ++k)
          energy += power[band.first + k] * aii::ecapa::mel[band.offset + k];
        const float db = 10.0f * std::log10(std::max(energy, 1e-10f));
        if (!std::isfinite(db))
          return 4;
        result[t * 80 + m] = db;
        peak = std::max(peak, db);
      }
    }
    std::array<double, 80> sums{};
    for (size_t t = 0; t < n; ++t) {
      if (stopped())
        return 3;
      for (size_t m = 0; m < 80; ++m) {
        auto &v = result[t * 80 + m];
        v = std::max(v, peak - 80.0f);
        sums[m] += v;
      }
    }
    for (size_t t = 0; t < n; ++t) {
      if (stopped())
        return 3;
      for (size_t m = 0; m < 80; ++m)
        result[t * 80 + m] -=
            static_cast<float>(sums[m] / static_cast<double>(n));
    }
    if (stopped())
      return 3;
    std::copy(result.begin(), result.end(), output);
    *frames = n;
    return 0;
  } catch (...) {
    return 4;
  }
}
