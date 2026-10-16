#include "echo.h"
#include "api/echo_canceller3_config.h"
#include "api/echo_canceller3_factory.h"
#include "api/echo_control.h"
#include "api/environment.h"
#include "audio_processing/audio_buffer.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <limits>
#include <memory>

namespace {
constexpr size_t frame = 160;
constexpr int rate = 16000;
struct Backend {
  webrtc::Environment env;
  std::unique_ptr<webrtc::EchoControl> control;
  webrtc::AudioBuffer far{rate, 1, rate, 1, rate, 1};
  webrtc::AudioBuffer near{rate, 1, rate, 1, rate, 1};
  Backend() {
    webrtc::EchoCanceller3Config config;
    // Keep upstream defaults; acoustic delay is estimated by AEC3, not a
    // made-up constant based on engine generation or network arrival time.
    control = webrtc::EchoCanceller3Factory(config).Create(env, rate, 1, 1);
    if (!control)
      throw std::bad_alloc();
  }
};
bool valid(const float *p, size_t count) {
  if (!p)
    return false;
  for (size_t i = 0; i < count; ++i)
    if (!std::isfinite(p[i]) || p[i] < -1.f || p[i] > 1.f)
      return false;
  return true;
}
bool overlaps(const float *a, const float *b) {
  if (!a || !b)
    return false;
  const auto x = reinterpret_cast<uintptr_t>(a),
             y = reinterpret_cast<uintptr_t>(b);
  return x < y ? y - x < frame * sizeof(float) : x - y < frame * sizeof(float);
}
} // namespace
struct aii_echo {
  aii_echo_status status{};
  std::unique_ptr<Backend> backend;
  unsigned tail = 0;
  // Bound extraction: BlockFramer contributes 64 samples and overlap/add
  // contributes 64. Another 32 makes one complete 160-sample external frame.
  std::array<float, 32> clean_delay{};
  std::array<float, 160> previous_raw{};
  bool pending = false, previous_process = false;
  size_t previous_count = 0;
  bool partial = false;
  explicit aii_echo(uint64_t generation) { status.generation = generation; }
};
extern "C" {
int aii_echo_create(uint64_t generation, aii_echo **out) {
  if (!out)
    return AII_ECHO_INVALID;
  *out = nullptr;
  if (!generation)
    return AII_ECHO_INVALID;
  try {
    *out = new aii_echo(generation);
    return AII_ECHO_OK;
  } catch (...) {
    return AII_ECHO_FAILURE;
  }
}
int aii_echo_reset(aii_echo *e, uint64_t generation) {
  if (!e || generation <= e->status.generation)
    return AII_ECHO_INVALID;
  e->backend.reset();
  e->tail = 0;
  e->status = {};
  e->status.generation = generation;
  e->clean_delay.fill(0);
  e->previous_raw.fill(0);
  e->pending = false;
  e->previous_process = false;
  e->previous_count = 0;
  e->partial = false;
  return AII_ECHO_OK;
}
int aii_echo_process(aii_echo *e, uint64_t generation, uint64_t start,
                     const float *capture, const float *render, size_t samples,
                     unsigned flags, float *output, size_t *output_samples) {
  if (!e || !output_samples || e->status.finished || e->partial ||
      samples == 0 || samples > frame || flags > AII_ECHO_REFERENCE_VALID ||
      !output || generation != e->status.generation ||
      start != e->status.next_sample ||
      start > std::numeric_limits<uint64_t>::max() - samples ||
      !valid(capture, samples) ||
      ((flags & AII_ECHO_REFERENCE_VALID) && !valid(render, samples)) ||
      overlaps(capture, output) || overlaps(render, output))
    return AII_ECHO_INVALID;
  try {
    const bool had_backend = bool(e->backend);
    std::array<float, frame> result{};
    bool process = false;
    if (!(flags & AII_ECHO_REFERENCE_VALID)) {
      e->backend.reset();
      e->tail = 0;
      e->clean_delay.fill(0);
      e->status.state = AII_ECHO_REFERENCE_MISSING;
      ++e->status.missing_reference_frames;
    } else {
      if (!e->backend)
        e->backend = std::make_unique<Backend>();
      auto &b = *e->backend;
      double energy = 0;
      for (size_t i = 0; i < frame; ++i) {
        // AudioBuffer uses float samples in signed-16-bit amplitude units.
        b.far.channels()[0][i] = i < samples ? render[i] * 32768.f : 0;
        b.near.channels()[0][i] = i < samples ? capture[i] * 32768.f : 0;
        if (i < samples)
          energy += double(render[i]) * render[i];
      }
      b.control->AnalyzeRender(&b.far);
      b.control->AnalyzeCapture(&b.near);
      b.control->ProcessCapture(&b.near, false);
      // A measured nonzero reference, including quiet output, extends the
      // tail. Silence outside that tail leaves ordinary microphone PCM exact.
      e->tail = energy > 0 ? 100 : (e->tail ? e->tail - 1 : 0);
      std::array<float, frame> cleaned{};
      for (size_t i = 0; i < frame; ++i) {
        const float v = b.near.channels_const()[0][i] / 32768.f;
        if (!std::isfinite(v)) {
          e->backend.reset();
          e->tail = 0;
          return AII_ECHO_FAILURE;
        }
        cleaned[i] = std::clamp(v, -1.f, 1.f);
      }
      std::copy(e->clean_delay.begin(), e->clean_delay.end(), result.begin());
      std::copy_n(cleaned.begin(), 128, result.begin() + 32);
      std::copy(cleaned.begin() + 128, cleaned.end(), e->clean_delay.begin());
      process = had_backend && e->previous_process;
      e->status.state = e->tail ? AII_ECHO_PROCESSING : AII_ECHO_PASSTHROUGH;
    }
    if (e->pending)
      std::copy_n(process ? result.data() : e->previous_raw.data(),
                  e->previous_count, output);
    *output_samples = e->pending ? e->previous_count : 0;
    e->previous_raw.fill(0);
    std::copy_n(capture, samples, e->previous_raw.begin());
    e->pending = true;
    e->previous_count = samples;
    e->partial = samples < frame;
    e->previous_process = (flags & AII_ECHO_REFERENCE_VALID) && e->tail;
    e->status.next_sample += samples;
    ++e->status.processed_frames;
    return AII_ECHO_OK;
  } catch (...) {
    e->backend.reset();
    e->tail = 0;
    return AII_ECHO_FAILURE;
  }
}
int aii_echo_finish(aii_echo *e, uint64_t generation, float *output,
                    size_t *samples) {
  if (!e || !output || !samples || generation != e->status.generation)
    return AII_ECHO_INVALID;
  if (e->status.finished || !e->pending) {
    *samples = 0;
    e->status.finished = 1;
    return AII_ECHO_OK;
  }
  const auto prior = e->status;
  const bool partial = e->partial;
  e->partial = false;
  const std::array<float, frame> zero{};
  const auto rc = aii_echo_process(
      e, generation, e->status.next_sample, zero.data(), zero.data(), frame,
      e->backend ? AII_ECHO_REFERENCE_VALID : 0, output, samples);
  if (rc) {
    e->partial = partial;
    return rc;
  }
  // Padding drains DSP only. Do not advance the admitted microphone clock.
  e->status = prior;
  e->status.finished = 1;
  e->pending = false;
  e->backend.reset();
  e->previous_raw.fill(0);
  e->clean_delay.fill(0);
  return AII_ECHO_OK;
}
int aii_echo_get_status(const aii_echo *e, aii_echo_status *out) {
  if (!e || !out)
    return AII_ECHO_INVALID;
  *out = e->status;
  return AII_ECHO_OK;
}
void aii_echo_destroy(aii_echo *e) { delete e; }
}
