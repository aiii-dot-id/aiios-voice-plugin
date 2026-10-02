#pragma once
// Private, opt-in attribution of the already loaded diagnostic TTS library.
// The synthesis owner samples only BETWEEN native calls, never resets global
// counters, loads a second library, or makes cancellation wait for telemetry.
// One record is emitted at retirement, after first PCM has already escaped.
#include <array>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#endif

namespace aii::voice {
class TtsPhaseTrace {
public:
  using Counters = std::array<uint64_t, 4>;
  using Query = int (*)(uint64_t*, size_t, int);
private:
  bool enabled_ = false, active_ = false, available_ = false;
  Query query_ = nullptr;
  FILE* sink_ = stderr;
  uint64_t client_ = 0, generation_ = 0, begin_ = 0, start_ = 0, first_ = 0;
  uint64_t first_samples_ = 0;
  int start_result_ = 0;
  Counters before_{}, prepared_{}, first_counters_{};
  static uint64_t now() noexcept {
    return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count());
  }
  bool read(Counters& value) noexcept {
    return query_ && query_(value.data(), value.size(), 0) == 0;
  }
  static void array(FILE* f, const Counters& a) noexcept {
    std::fprintf(f, "[%llu,%llu,%llu,%llu]", (unsigned long long)a[0],
        (unsigned long long)a[1], (unsigned long long)a[2], (unsigned long long)a[3]);
  }
public:
  TtsPhaseTrace() noexcept {
    const char* flag = std::getenv("AII_VOICE_TTS_PHASE_TRACE");
    enabled_ = flag && std::strcmp(flag, "1") == 0;
#ifdef _WIN32
    if (enabled_) {
      const auto module = GetModuleHandleW(L"native_pocket_resident.dll");
      if (module) query_ = reinterpret_cast<Query>(GetProcAddress(module, "nv_profile_snapshot"));
    }
#endif
  }
  // Deterministic native probe injection; not a plugin or model API.
  TtsPhaseTrace(bool enabled, Query query, FILE* sink) noexcept
      : enabled_(enabled), query_(query), sink_(sink) {}
  void begin(uint64_t client, uint64_t generation) noexcept {
    if (!enabled_) return;
    client_ = client; generation_ = generation; active_ = true;
    start_ = first_ = first_samples_ = 0; start_result_ = 0;
    before_ = {}; prepared_ = {}; first_counters_ = {};
    available_ = read(before_); begin_ = now();
  }
  void started(int result) noexcept {
    if (!active_) return;
    start_ = now(); start_result_ = result;
    available_ = read(prepared_) && available_;
  }
  void audio(size_t samples) noexcept {
    if (!active_ || first_ || !samples) return;
    first_ = now(); first_samples_ = samples;
    available_ = read(first_counters_) && available_;
  }
  void finish() noexcept {
    if (!active_) return;
    const auto retired = now(); Counters end{};
    available_ = read(end) && available_;
    std::fprintf(sink_, "{\"component\":\"native-tts-phases\",\"available\":%s,"
        "\"client\":%llu,\"generation\":%llu,\"begin_ns\":%llu,\"start_ns\":%llu,"
        "\"first_ns\":%llu,\"retired_ns\":%llu,\"first_samples\":%llu,\"start_result\":%d,\"before\":",
        available_ ? "true" : "false", (unsigned long long)client_, (unsigned long long)generation_,
        (unsigned long long)begin_, (unsigned long long)start_, (unsigned long long)first_,
        (unsigned long long)retired, (unsigned long long)first_samples_, start_result_);
    array(sink_, before_); std::fputs(",\"prepared\":", sink_); array(sink_, prepared_);
    std::fputs(",\"first\":", sink_); array(sink_, first_counters_);
    std::fputs(",\"retired\":", sink_); array(sink_, end); std::fputs("}\n", sink_);
    active_ = false;
  }
};
}
