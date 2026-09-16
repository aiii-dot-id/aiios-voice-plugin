#include "frontend.h"
#include <algorithm>
#include <cmath>
#include <complex>
#include <limits>
#include <stdexcept>

namespace aii::asr {
namespace {
constexpr double pi = 3.1415926535897932384626433832795;
void fft(std::array<std::complex<double>, 512>& v) {
  for (size_t i = 1, j = 0; i < v.size(); ++i) {
    size_t bit = v.size() >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) std::swap(v[i], v[j]);
  }
  for (size_t n = 2; n <= v.size(); n <<= 1) {
    const auto root = std::polar(1.0, -2 * pi / static_cast<double>(n));
    for (size_t first = 0; first < v.size(); first += n) {
      std::complex<double> w(1, 0);
      for (size_t j = 0; j < n / 2; ++j) {
        const auto left = v[first+j], right = w * v[first+j+n/2];
        v[first+j] = left + right;
        v[first+j+n/2] = left - right;
        w *= root;
      }
    }
  }
}
}
Frontend::Frontend(const float* mel, size_t count) : mel_(128 * 257) {
  if (!mel || count != mel_.size()) throw std::invalid_argument("mel extent");
  for (size_t i = 0; i < count; ++i) {
    if (!std::isfinite(mel[i]) || mel[i] < 0) throw std::invalid_argument("mel value");
    mel_[i] = mel[i];
  }
  for (size_t i = 0; i < 400; ++i)
    window_[i+56] = static_cast<float>((1 - std::cos(2 * pi * i / 399.0)) / 2);
  pcm_.reserve(kCapacity);
}
void Frontend::accept(const float* samples, size_t count) {
  if (finished_) throw std::runtime_error("feature input closed");
  if (!samples || count == 0 || count > 32000) throw std::invalid_argument("PCM extent");
  if (count > kCapacity - pcm_.size()) throw std::overflow_error("PCM capacity");
  if (count > std::numeric_limits<size_t>::max() - this->samples()) throw std::overflow_error("PCM clock overflow");
  for (size_t i = 0; i < count; ++i)
    if (!std::isfinite(samples[i])) throw std::invalid_argument("nonfinite PCM");
  pcm_.insert(pcm_.end(), samples, samples + count);
}
void Frontend::discard_before(size_t absolute_sample) {
  if (absolute_sample < base_ || absolute_sample > samples()) throw std::invalid_argument("discard outside retained PCM");
  const auto count = absolute_sample - base_;
  pcm_.erase(pcm_.begin(), pcm_.begin()+count);
  base_ = absolute_sample;
}
void Frontend::finish() {
  if (finished_ || !samples()) throw std::runtime_error("empty or closed feature input");
  finished_ = true;
}
size_t Frontend::frames_ready() const {
  if (finished_) return samples() / 160;
  return samples() < 256 ? 0 : (samples() - 256) / 160 + 1;
}
std::vector<float> Frontend::frames(size_t first, size_t count) const {
  if (count == 0 || count > 128 || first > frames_ready() || count > frames_ready() - first)
    throw std::invalid_argument("unavailable feature frames");
  // Pre-emphasis needs the sample immediately BEFORE the first window sample.
  const size_t needed = first*160 > 256 ? first*160-257 : 0;
  if (needed < base_) throw std::invalid_argument("feature window was retired");
  std::vector<float> result(count * 128);
  for (size_t frame = 0; frame < count; ++frame) {
    std::array<std::complex<double>, 512> spectrum{};
    const auto start = static_cast<int64_t>((first + frame) * 160) - 256;
    for (size_t i = 0; i < 512; ++i) {
      const auto index = start + static_cast<int64_t>(i);
      float value = 0;
      if (index >= 0 && static_cast<size_t>(index) < samples()) {
        value = pcm_[static_cast<size_t>(index)-base_];
        if (index > 0) value -= 0.97f * pcm_[static_cast<size_t>(index)-1-base_];
      }
      // Float multiplication before the double FFT matches the reference.
      spectrum[i] = static_cast<double>(value * window_[i]);
    }
    fft(spectrum);
    std::array<float, 257> power{};
    for (size_t i = 0; i < power.size(); ++i) power[i] = static_cast<float>(std::norm(spectrum[i]));
    for (size_t m = 0; m < 128; ++m) {
      float energy = 0;
      for (size_t k = 0; k < power.size(); ++k) energy += power[k] * mel_[m*257+k];
      const auto value = std::log(energy + 0x1p-24f);
      if (!std::isfinite(value)) throw std::runtime_error("nonfinite feature output");
      result[frame*128+m] = value;
    }
  }
  return result;
}
}  // namespace aii::asr
