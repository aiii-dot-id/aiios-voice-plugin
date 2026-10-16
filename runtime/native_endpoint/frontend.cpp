#include "frontend.h"
#include "fft.h"
#include "math.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdlib>
#include <memory>
#include <stdexcept>
#ifdef AII_ENDPOINT_VECTOR_FRONTEND
#ifndef __aarch64__
#error "The explicitly selected vector frontend requires ARM64"
#endif
#include <arm_neon.h>
#endif
#ifdef AII_ENDPOINT_ATEN
#include <ATen/ATen.h>
#include <ATen/Parallel.h>
#include <c10/core/InferenceMode.h>
#endif

namespace aii::endpoint {
namespace {
constexpr size_t window = 128000;
// Pairwise float32 reduction layout used by the reference's contiguous NumPy
// normalization. This is arithmetic, not a learned input-specific correction.
float sum(const float* x, size_t n) {
  if (n < 8) { float r = -0.0f; for (size_t i = 0; i < n; ++i) r += x[i]; return r; }
  if (n <= 128) {
    float r[8]; std::copy(x, x + 8, r);
    size_t i = 8;
    for (; i + 7 < n; i += 8) for (size_t j = 0; j < 8; ++j) r[j] += x[i+j];
    float total = ((r[0]+r[1])+(r[2]+r[3])) + ((r[4]+r[5])+(r[6]+r[7]));
    for (; i < n; ++i) total += x[i];
    return total;
  }
  size_t half = n / 2; half -= half % 8;
  return sum(x, half) + sum(x + half, n - half);
}
void check(const std::function<bool()>& cancelled) {
  if (cancelled()) throw std::runtime_error("endpoint cancelled");
}
}
Frontend::Frontend(const float* coefficients, size_t count) {
  if (!coefficients || count != 201*80+400) throw std::invalid_argument("endpoint coefficient extent");
  for (size_t i = 0; i < count; ++i)
    if (!std::isfinite(coefficients[i]) || coefficients[i] < 0)
      throw std::invalid_argument("endpoint coefficient value");
  for (size_t band = 0; band < 80; ++band) {
    bool nonzero = false;
    for (size_t k = 0; k < 201; ++k) nonzero |= coefficients[k*80+band] > 0;
    if (!nonzero) throw std::invalid_argument("empty endpoint mel filter");
  }
  if (coefficients[201*80] != 0 || coefficients[201*80+200] != 1)
    throw std::invalid_argument("endpoint Hann endpoints");
  coefficients_.assign(coefficients, coefficients + count);
#ifdef AII_ENDPOINT_SPARSE_FRONTEND
#ifdef AII_ENDPOINT_VECTOR_FRONTEND
  // One ordered union for four independent bands: zero lanes may remain,
  // but bins zero in all four bands need no vector fused operation.
  for (size_t band = 0; band < 80; band += 4)
    for (size_t k = 0; k < 201; ++k)
      for (size_t lane = 0; lane < 4; ++lane)
        if (coefficients_[k*80+band+lane] != 0) {
          nonzero_bins_[band].push_back(k);
          break;
        }
#else
  for (size_t band = 0; band < 80; ++band)
    for (size_t k = 0; k < 201; ++k)
      if (coefficients_[k*80+band] != 0)
        nonzero_bins_[band].push_back(k);
#endif
#endif
#ifdef AII_ENDPOINT_ATEN
  // Explicit candidate backend. Its bound coefficients must describe this
  // platform's window; silently substituting a different window is forbidden.
  const auto hann = at::hann_window(400, at::TensorOptions().dtype(at::kFloat));
  if (!std::equal(coefficients + 201*80, coefficients + count, hann.const_data_ptr<float>()))
    throw std::invalid_argument("endpoint Hann differs from bound ATen backend");
#endif
}
std::vector<float> Frontend::features(const float* samples, size_t count,
                                    const std::function<bool()>& cancelled) const {
  if (!samples || !count || count > 960000) throw std::invalid_argument("bounded nonempty endpoint PCM required");
  for (size_t i = 0; i < count; ++i) if (!std::isfinite(samples[i])) throw std::invalid_argument("endpoint PCM not finite");
  check(cancelled);
  // Keep the last eight seconds, LEFT pad first, then normalize the whole window.
  const auto kept = std::min(window, count);
  std::vector<float> audio(window, 0), squares(window);
  std::copy(samples + count - kept, samples + count, audio.end() - kept);
  const float mean = sum(audio.data(), window) / static_cast<float>(window);
  for (size_t i = 0; i < window; ++i) { audio[i] -= mean; squares[i] = audio[i]*audio[i]; }
  const float variance = sum(squares.data(), window) / static_cast<float>(window);
  const float scale = std::sqrt(variance + 1e-7f);
  if (!std::isfinite(scale) || scale <= 0) throw std::runtime_error("endpoint normalization failure");
  for (auto& value : audio) value /= scale;
#ifdef AII_ENDPOINT_ATEN
  // CPU-only frontend, not a Python embedding. Match the frozen Windows
  // reference kernels rather than attributing a quantized model's threshold
  // changes to harmless floating-point noise. Mac's admitted path is unchanged.
  c10::InferenceMode inference_guard;
  at::set_num_threads(1);
  const auto options = at::TensorOptions().dtype(at::kFloat).device(at::kCPU);
  auto input = at::from_blob(audio.data(), {1, 128000}, options);
  auto hann = at::from_blob(const_cast<float*>(coefficients_.data()+201*80), {400}, options);
  auto filters = at::from_blob(const_cast<float*>(coefficients_.data()), {201,80}, options);
  check(cancelled);
  auto spectra = at::stft(input, 400, 160, std::nullopt, hann, true, "reflect", false, std::nullopt, true);
  check(cancelled);
  auto power = spectra.slice(-1, 0, 800).abs().pow(2);
  auto mel = at::matmul(filters.transpose(0, 1), power);
  check(cancelled);
  auto logs = mel.clamp_min(1e-10).log10();
  auto result = ((at::maximum(logs, logs.max()-8)+4)/4).contiguous();
  check(cancelled);
  const auto data = result.const_data_ptr<float>();
  std::vector<float> output(data, data+64000);
  if (!std::all_of(output.begin(), output.end(), [](float v) { return std::isfinite(v); }))
    throw std::runtime_error("endpoint feature failure");
  return output;
#else
  std::vector<float> signals(801*400);
  for (size_t frame=0; frame<801; ++frame) {
    for (int i=0; i<400; ++i) {
      auto at=static_cast<int>(frame*160)+i-200;
      if (at<0) at=-at;
      if (at>=static_cast<int>(window)) at=static_cast<int>(2*window)-2-at;
      signals[frame*400+i]=audio[at]*coefficients_[201*80+i];
    }
  }
  check(cancelled);
  std::vector<std::complex<float>> spectra(801*201);
  fft_frames(signals.data(),spectra.data());
  std::vector<float> output(80*800);
  float maximum = -INFINITY;
  for (size_t frame = 0; frame < 800; ++frame) {
    check(cancelled);
    std::array<float, 201> power{};
    for (size_t k = 0; k < power.size(); ++k) {
      const float magnitude = std::abs(spectra[frame*201+k]);
      power[k] = magnitude * magnitude;
#ifdef AII_ENDPOINT_SPARSE_FRONTEND
      // Zero times infinity/NaN is not an identity. Refuse before omitting
      // zero coefficients, including bins outside every filter's support.
      if (!std::isfinite(power[k]))
        throw std::runtime_error("endpoint feature failure");
#endif
    }
#ifdef AII_ENDPOINT_VECTOR_FRONTEND
    // Four independent bands, not a reduction reassociation: every lane keeps
    // the exact k=0..200 fused operation sequence of the scalar implementation.
    for (size_t band = 0; band < 80; band += 4) {
      auto mel = vdupq_n_f32(0);
#ifdef AII_ENDPOINT_SPARSE_FRONTEND
      for (const auto k : nonzero_bins_[band])
#else
      for (size_t k = 0; k < 201; ++k)
#endif
        mel = vfmaq_n_f32(mel, vld1q_f32(coefficients_.data()+k*80+band), power[k]);
      float values[4], logs[4];
      vst1q_f32(values, vmaxq_f32(mel, vdupq_n_f32(1e-10f)));
      reference_log10_four(values, logs);
      for(size_t lane=0;lane<4;++lane) {
        const auto value=logs[lane];
        if (!std::isfinite(value)) throw std::runtime_error("endpoint feature failure");
        output[(band+lane)*800+frame] = value; maximum = std::max(maximum, value);
      }
    }
#else
    for (size_t band = 0; band < 80; ++band) {
      float mel = 0;
      // The admitted matrix product rounds each multiply-add once. Separate
      // multiplication/addition introduces an extra float32 rounding which
      // the quantized endpoint can amplify even when the FFT is bit-exact.
#ifdef AII_ENDPOINT_SPARSE_FRONTEND
      // Nonnegative finite power and validated nonnegative coefficients:
      // fma(0, power, mel) cannot change this nonnegative accumulator. Skip
      // only exact zeros; every nonzero FMA retains its original bin order.
      for (const auto k : nonzero_bins_[band])
#else
      for (size_t k = 0; k < 201; ++k)
#endif
        mel = std::fma(coefficients_[k*80+band], power[k], mel);
      const auto value = reference_log10(std::max(mel, 1e-10f));
      if (!std::isfinite(value)) throw std::runtime_error("endpoint feature failure");
      output[band*800+frame] = value; maximum = std::max(maximum, value);
    }
#endif
  }
  for (auto& value : output) value = (std::max(value, maximum - 8.0f) + 4.0f) / 4.0f;
  return output;
#endif
}
}
