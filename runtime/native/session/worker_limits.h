#pragma once
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <initializer_list>
#include <stdexcept>
#include <string>

namespace aii::voice {
struct WorkerLimits {
  std::chrono::milliseconds exchange_read{5500};
  std::chrono::milliseconds exchange_write{12500};
  std::chrono::milliseconds opening{12500};
  std::chrono::milliseconds whole_read{12500};
  std::chrono::milliseconds whole_publication{50000};
  std::chrono::milliseconds drain_idle{15000};
  std::chrono::milliseconds reply_settings{150};
  std::chrono::milliseconds abort{5000};
  std::chrono::milliseconds capture_close{45000};
  std::chrono::milliseconds session_open{60000};
  std::chrono::milliseconds opening_notice{1500};
  std::chrono::milliseconds audio_write{3000};
  std::chrono::milliseconds control_write{3000};
  std::chrono::milliseconds retire{5000};
  std::chrono::milliseconds capture_tail{2000};
  std::chrono::milliseconds model_call{30000};
  std::chrono::milliseconds input_tail{3000};
  std::chrono::milliseconds output_take{15000};
  std::chrono::milliseconds speaker_match{15000};
  std::chrono::milliseconds warm_probe{40000};
  std::chrono::milliseconds endpoint_decision{1000};
  std::chrono::milliseconds endpoint_retire{30500};
  std::chrono::milliseconds separation_min{4000};
  std::chrono::milliseconds separation_max{25000};

  static constexpr const char* variable = "AII_VOICE_LIMITS";
  static constexpr int64_t floor_ms = 250, ceiling_ms = 600000;
  static constexpr int64_t reply_floor_ms = 10, reply_ceiling_ms = 2000;

  void validate() const {
    const auto in_range = [](std::chrono::milliseconds v) { return v.count() >= floor_ms && v.count() <= ceiling_ms; };
    if (!in_range(exchange_read) || !in_range(exchange_write) || !in_range(opening) || !in_range(whole_read) || !in_range(whole_publication) || !in_range(drain_idle) ||
        !in_range(abort) || !in_range(capture_close) || !in_range(session_open) || !in_range(opening_notice) || !in_range(audio_write) ||
        !in_range(control_write) || !in_range(retire) || !in_range(capture_tail) || !in_range(model_call) || !in_range(input_tail) ||
        !in_range(output_take) || !in_range(speaker_match) || !in_range(warm_probe) || !in_range(endpoint_retire) ||
        !in_range(separation_min) || !in_range(separation_max))
      throw std::runtime_error("worker limits out of range");
    for (const auto listener : {reply_settings, endpoint_decision})
      if (listener.count() < reply_floor_ms || listener.count() > reply_ceiling_ms)
        throw std::runtime_error("worker limits out of range");
    if (exchange_write < exchange_read || whole_read < exchange_read || whole_publication < exchange_write + whole_read)
      throw std::runtime_error("worker limits do not nest");
  }

  static WorkerLimits parse(const std::string& text) {
    WorkerLimits limits;
    struct Member { const char* name; std::chrono::milliseconds* value; bool seen; };
    Member members[] = {{"exchange_read_ms", &limits.exchange_read, false}, {"exchange_write_ms", &limits.exchange_write, false},
                        {"opening_ms", &limits.opening, false}, {"whole_read_ms", &limits.whole_read, false},
                        {"whole_publication_ms", &limits.whole_publication, false}, {"drain_idle_ms", &limits.drain_idle, false},
                        {"reply_settings_ms", &limits.reply_settings, false}, {"abort_ms", &limits.abort, false},
                        {"capture_close_ms", &limits.capture_close, false}, {"session_open_ms", &limits.session_open, false},
                        {"opening_notice_ms", &limits.opening_notice, false}, {"audio_write_ms", &limits.audio_write, false},
                        {"control_write_ms", &limits.control_write, false}, {"retire_ms", &limits.retire, false},
                        {"capture_tail_ms", &limits.capture_tail, false}, {"model_call_ms", &limits.model_call, false},
                        {"input_tail_ms", &limits.input_tail, false}, {"output_take_ms", &limits.output_take, false},
                        {"speaker_match_ms", &limits.speaker_match, false}, {"warm_probe_ms", &limits.warm_probe, false},
                        {"endpoint_decision_ms", &limits.endpoint_decision, false}, {"endpoint_retire_ms", &limits.endpoint_retire, false},
                        {"separation_min_ms", &limits.separation_min, false}, {"separation_max_ms", &limits.separation_max, false}};
    const auto bad = [] { return std::runtime_error("malformed worker limits"); };
    size_t i = 0;
    const auto expect = [&](char c) { if (i >= text.size() || text[i] != c) throw bad(); ++i; };
    expect('{');
    for (;;) {
      expect('"');
      const auto end = text.find('"', i);
      if (end == std::string::npos) throw bad();
      const auto name = text.substr(i, end - i);
      i = end + 1;
      expect(':');
      const auto digits = i;
      int64_t value = 0;
      while (i < text.size() && text[i] >= '0' && text[i] <= '9') {
        if (i - digits >= 9) throw bad();
        value = value * 10 + (text[i++] - '0');
      }
      if (i == digits || (text[digits] == '0' && i - digits > 1)) throw bad();
      Member* found = nullptr;
      for (auto& m : members) if (name == m.name) found = &m;
      if (!found || found->seen) throw bad();
      found->seen = true;
      *found->value = std::chrono::milliseconds(value);
      if (i < text.size() && text[i] == ',') { ++i; continue; }
      break;
    }
    expect('}');
    if (i != text.size()) throw bad();
    for (const auto& m : members) if (!m.seen) throw bad();
    limits.validate();
    return limits;
  }

  static WorkerLimits from_environment() {
#ifdef _WIN32
    char* value = nullptr; size_t length = 0;
    if (_dupenv_s(&value, &length, variable)) throw std::runtime_error("cannot read worker limits");
    std::string text = value ? value : "";
    std::free(value);
#else
    const char* value = std::getenv(variable);
    std::string text = value ? value : "";
#endif
    if (text.empty()) return WorkerLimits{};
    return parse(text);
  }
};
}
