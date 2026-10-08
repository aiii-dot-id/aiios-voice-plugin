#pragma once
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <initializer_list>
#include <stdexcept>
#include <string>

namespace aii::voice {
// THE TIME LIMITS THE WORKER WAITS BY. For what it asks of its carrier: one
// exchange of the private files (a read, or a durable write), a session's
// settings at its opening, one whole read of a file, one whole publication.
// They were numbers typed where they were used (2 s an exchange, 10 s a read,
// 30 s a publication, 2 s the settings), beside a carrier that gave the host
// 1.5 s for anything. On a disk that was slow for a moment the worker gave
// up: a session refused at its opening, a speaker reported unavailable, a
// profile not stored. And for its own work: a session's end, a reply's wait
// for its settings, an abort, a capture's close, a session's open, when an
// opening says what it waits for, one write of audio to the host's pipe, one
// write of a line to its carrier, a capture's last frames and its own end.
// And for the engine it runs, which it hands its numbers on to: one model
// call, a conversation's last frames, synthesized audio waiting to be taken,
// a final's wait for its speaker, the warm inference of its start, the
// endpoint's verdict and its questions at the input's end, and the two bounds
// of a separation's budget.
//
// The carrier owns the table (plugin/native/limits.go) and states its
// limits there. From three of them, the host's storage times, it computes
// the first five members here so that every outer one covers what is inside
// it. The others here it hands on as it states them. The rest it keeps to
// itself, because only it waits by them: a control's answer, this worker's
// readiness, and the three waits of its own end.
// It hands the members below over in AII_VOICE_LIMITS at the worker's
// start. The worker checks that they are in range and nest, and does no
// arithmetic of its own on them. The defaults here are what the carrier's
// defaults compute to, held equal by a test on the carrier's side; they
// serve a worker started with no carrier (a probe, a test).
struct WorkerLimits {
  std::chrono::milliseconds exchange_read{5500};       // the carrier's answer to one page read
  std::chrono::milliseconds exchange_write{12500};     // the carrier's answer to one stage or publish
  std::chrono::milliseconds opening{12500};            // a session's settings
  std::chrono::milliseconds whole_read{12500};         // every page of one file and its readback
  std::chrono::milliseconds whole_publication{50000};  // the stages, the publish, the read that verifies
  std::chrono::milliseconds drain_idle{15000};         // a session ending with nothing moving and nothing in flight (drain_hold.h)
  std::chrono::milliseconds reply_settings{150};       // a reply's first segment waiting for the settings in force
  std::chrono::milliseconds abort{5000};               // an aborted session's core retiring, counted from the first abort
  std::chrono::milliseconds capture_close{45000};      // an enrollment capture's close, its preparation included
  std::chrono::milliseconds session_open{60000};       // a session's open returning, a speaking model's load included
  std::chrono::milliseconds opening_notice{1500};      // an opening that has waited this long says what it waits for
  // One write of audio to the host's pipe. Past it the pipe is called stalled
  // and the worker retires (worker_io.h). It was three seconds typed in the
  // pipe, beside a carrier that gave a playback report, which is held until
  // the audio write it counts has ended, two.
  std::chrono::milliseconds audio_write{3000};
  // One write of a line to the carrier. Past it the control channel is called
  // stalled and the worker retires. It was three seconds typed in the pipe.
  std::chrono::milliseconds control_write{3000};
  // The worker's own end, counted from when it begins to end: its input has
  // ended, or it has failed. Past it the worker says what it was still
  // waiting for and ends with status 72. The carrier waits longer than this
  // for the worker's exit, by its margin. It was five seconds typed where the
  // main loop ends and five more where the threads are joined.
  std::chrono::milliseconds retire{5000};
  // The frames of an enrollment capture up to the sample it was told it ends
  // at, counted from when it was told. It was two seconds typed in the worker.
  std::chrono::milliseconds capture_tail{2000};
  // THE ENGINE'S OWN WAITS. They were numbers typed in the session core, the
  // speaker attribution and the model owner; the worker hands each on where
  // it is waited by (worker.cpp: aii_voice_open_bounded, Attributions,
  // aii_voice_models_warm_within).
  //
  // One synchronous model inference stage, by the session's watchdog.
  std::chrono::milliseconds model_call{30000};
  // The frames of a conversation up to the sample it was told it ends at.
  std::chrono::milliseconds input_tail{3000};
  // Synthesized audio waiting for room in the session's bounded queue: how
  // long this worker has to take what is there. The carrier holds it over
  // audio_write and its margin, so a write the host has stopped taking is
  // said by the pipe's own deadline first.
  std::chrono::milliseconds output_take{15000};
  // A final's wait for its speaker before the wait is declared over.
  std::chrono::milliseconds speaker_match{15000};
  // The warm inference of this worker's start. The carrier refuses a report
  // of one that took longer, by the same number.
  std::chrono::milliseconds warm_probe{40000};
  // The endpoint model's verdict at a turn's commit point, counted from its
  // query's submission. A listener's wait: past it the turn ends by silence
  // alone, and the worker's log says so.
  std::chrono::milliseconds endpoint_decision{1000};
  // Each endpoint query still owned when a conversation's input ends. The
  // query is a model call, so the carrier holds this over model_call and its
  // margin: the call's own limit speaks first.
  std::chrono::milliseconds endpoint_retire{30500};
  // The two bounds of the budget one separation of competing talkers has,
  // which is five times the separated audio's length between them. The
  // carrier holds model_call over the longer and its margin.
  std::chrono::milliseconds separation_min{4000};
  std::chrono::milliseconds separation_max{25000};

  static constexpr const char* variable = "AII_VOICE_LIMITS";
  static constexpr int64_t floor_ms = 250, ceiling_ms = 600000;
  // A reply's wait for its settings and a turn's for the endpoint's verdict
  // are a listener's, not storage's: their own range.
  static constexpr int64_t reply_floor_ms = 10, reply_ceiling_ms = 2000;

  // In range, and nesting: a write is given at least a read's time; a whole
  // read covers one exchange; a whole publication covers a write and a
  // whole read.
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

  // Exactly the object the carrier writes: every member below, each a whole
  // number of milliseconds, each once, nothing else. Strict on purpose: a
  // table that is not understood is refused, never half applied.
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
        if (i - digits >= 9) throw bad();  // far past the ceiling; no overflow
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

  // What the carrier handed over, or the defaults when no carrier did.
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
} // namespace aii::voice
