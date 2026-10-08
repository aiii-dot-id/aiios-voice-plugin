// Private Go-carrier worker. Public JSON-RPC remains solely in the Go SDK.
// This process has no Python, microphone/speaker, network or enrollment store.
#include "c_api.h"
#include "worker_io.h"
#include "worker_audio_scratch.h"
#include "worker_liveness.h"
#include "worker_environment.h"
#include "worker_json.h"
#include "speaker_observation.h"
#include "attribution.h"
#include "snapshot_bridge.h"
#include "speaker_registry_store.h"
#include "speaker_readback.h"
#include "capture_enrollment.h"
#include "uid_recovery.h"
#include "capture_input.h"
#include "waveform_capture.h"
#include "../../native_uid/enrollment.h"
#include "../../native_uid/bound_policies.h"
#include "../vendor/picosha2/picosha2.h"
#include "operator_settings.h"
#include "corrections_wire.h"
#include "installed_profile.h"
#include "confirmed_acts.h"
#ifdef AII_WITH_ECHO
#include "echo_input.h"
#endif
#include <fstream>
#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <deque>
#include <future>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <thread>
#include <vector>
#include <unordered_map>
#include <unordered_set>
#include <array>
using namespace aii::voice::wire;
using Clock = std::chrono::steady_clock;
#ifndef AII_WORKER_BACKEND
#define AII_WORKER_BACKEND "native-common-cpu"
#endif
#ifdef AII_AUDIO_ACK_TEST_HOOK
// Fixture target only (worker_test_models.cpp): publication-order and
// audio-deadline detection-order seams, and the place between a pass's poll
// for the core's events and its read of the core's status.
namespace aii::voice::wire { void before_audio_ack(bool end, bool failed); bool main_loop_sees_audio_deadline(); void after_event_poll(bool draining); }
#endif
namespace {
// The worker's own deadline has passed: end now. On Windows that never waits
// behind DLL process-detach work (worker_io.h end_process).
[[noreturn]] void abandon(int code) {
#ifdef _WIN32
  aii::voice::wire::end_process(code);
#else
  std::_Exit(code);
#endif
}
const char *backend_name(const aii_voice_readiness &r) {
  if(std::string(r.accelerator)=="cpu_vulkan")return "native-common-vulkan";
  if(std::string(r.accelerator)=="cpu_metal")return "native-common-metal";
  return AII_WORKER_BACKEND;
}
void core(aii_voice_result r, const aii_voice_error &e) {
  if (r == AII_VOICE_INVALID)
    throw Refused(e.message);
  if (r != AII_VOICE_OK)
    throw std::runtime_error(e.message[0] ? e.message
                                          : "native ownership unavailable");
}
uint64_t be(const unsigned char *p, unsigned n) {
  uint64_t v = 0;
  for (unsigned i = 0; i < n; ++i)
    v = v * 256 + p[i];
  return v;
}
void be(unsigned char *p, uint64_t v, unsigned n) {
  while (n) {
    p[--n] = uint8_t(v);
    v >>= 8;
  }
}
struct Frame {
  uint32_t stream = 0, seq = 0;
  uint64_t start = 0;
  uint8_t kind = 0;
  std::vector<float> pcm;
  bool prepared = false;
  uint64_t feed_start = 0, input_count = 0;
  std::vector<float> clean;
};
struct Generation {
  std::string id;
  uint32_t stream = 0, seq = 0;
  uint64_t core_seen = 0, delivered = 0, rendered = 0, observed_generated = 0;
  bool ended = false, receipt = false, terminal_event = false, observed_retired = false, core_terminal_seen = false;
  std::atomic<bool> fenced{false};
};
struct Output {
  aii_voice_audio audio{};
  std::vector<float> pcm;
  std::shared_ptr<Generation> g;
  uint64_t start = 0;
  uint32_t seq = 0;
};
struct Ack {
  uint64_t samples = 0;
  uint32_t frames = 0;
  bool end = false;
  std::string error;
};
using IdentityDigest=std::array<unsigned char,32>;
IdentityDigest identity_digest(const std::string& id){
  IdentityDigest digest{};picosha2::hash256(id.begin(),id.end(),digest.begin(),digest.end());return digest;
}
struct IdentityHash {
  size_t operator()(const IdentityDigest& digest)const noexcept {
    size_t value=0;std::memcpy(&value,digest.data(),sizeof value);return value;
  }
};
// Opaque public identifiers cannot be forgotten while still refusing reuse.
// Retain compact cryptographic fences, not job objects, text, audio or polling
// work. A hash collision conservatively refuses admission, never aliases work.
struct TerminalReceipt {
  uint64_t epoch=0,rendered=0;
  uint32_t stream=0;
  bool settled=false;
};
class Worker {
  aii_voice_models *models_;
  aii::voice::SnapshotBridge& uid_snapshot_;
  std::unique_ptr<aii::voice::SpeakerRegistryStore> registry_;
  aii_voice_readiness readiness_;
  Pipe controls_, wire_, input_, output_;
  std::atomic<bool> readers_stop_{false}, io_stop_{false};
  std::atomic<unsigned> live_threads_{0};
  std::mutex mutex_;
  std::condition_variable changed_;
  std::vector<std::thread> threads_;
  std::deque<std::string> controls_in_, wire_out_;
  std::deque<Frame> audio_in_;
  size_t wire_bytes_ = 0, audio_samples_ = 0;
  bool eof_ = false, audio_eof_ = false, settled_ = false;
  std::string transport_fault_;
  std::optional<Output> output_task_;
  std::optional<Ack> ack_;
  std::deque<std::pair<uint64_t, Json>> unreconciled_; // request, report
  aii_voice_session *session_ = nullptr;
#ifdef AII_WITH_ECHO
  std::unique_ptr<aii::voice::EchoInput> echo_;
  std::optional<uint64_t> echo_cutoff_;
  std::vector<float> echo_tail_;
  uint64_t echo_tail_start_=0;
#endif
  bool reference_input_=false;
  std::future<aii_voice_session *> opening_;
  std::future<Json> enrollment_;
  std::optional<aii::voice::CaptureInput> capture_;
  std::future<Json> capturing_;
  std::future<Json> waveform_publish_;
  aii::voice::RecentWaveforms recent_waveforms_;
  std::string waveform_id_, waveform_session_, waveform_state_="none", waveform_sha_, waveform_reason_;
  uint64_t waveform_request_=0, waveform_start_=0, waveform_end_=0;
  size_t waveform_saved_samples_=0;
  std::atomic<bool> capture_cancelled_{false};
  Json capture_result_=null();
  Clock::time_point capture_tail_deadline_;
  uint64_t enrollment_request_=0;
  std::optional<aii::uid::BoundPolicies> uid_policies_;
  aii::voice::ConfirmedActs enrollment_acts_;
  std::string sid_, input_handle_, lifecycle_ = "closed", failure_;
  std::unordered_set<IdentityDigest,IdentityHash> used_sessions_;
  std::unordered_map<IdentityDigest,TerminalReceipt,IdentityHash> synthesis_identities_;
  uint64_t session_epoch_=0,settled_delivered_=0,settled_rendered_=0;
  std::string current_name_;
  std::map<uint64_t, std::string> issued_settings_;
  Attributions attributions_;
  std::map<uint64_t,uint64_t> enrollment_finals_; // public final -> native final
  std::map<uint64_t, std::shared_ptr<Generation>> generations_;
  uint64_t sequence_ = 0, request_id_ = 0, settings_id_ = 0,
           input_received_ = 0, current_ = 0, input_final_sequence_ = 0;
  uint32_t stream_counter_ = 0, input_stream_ = 0, input_seq_ = 0;
  bool input_enabled_ = true, input_started_ = false, end_seen_ = false;
  uint64_t input_limit_ = 0;
  // A finish taken while the session was still opening, applied when it opens.
  std::optional<uint64_t> early_finish_;
  bool capture_limit_reached_ = false;
  // A gap the host declared (AUD1 kind 2) is being filled with the silence it
  // replaced; gap_target_ is the sample the stream resumes at.
  bool gap_open_ = false;
  uint64_t gap_target_ = 0;
  // The input streams of sessions that ended. A leftover frame of one can
  // still be in the pipe when the next session opens. Stream ids are the
  // host's to choose and may be reused by the next session (the SDK proof
  // host numbers them all alike), so a frame is stale only when it also
  // cannot be the new session's first: that one starts at sample 0.
  std::deque<uint32_t> retired_streams_;
  // The AUD1 stream an open declared for its input (audio.input.stream).
  // Frames on any other stream are an earlier session's unread audio: they
  // are dropped and counted, never adopted. Each number serves one session,
  // so a frame on a number an earlier session declared is dropped by every
  // later session, including one that declares none.
  std::optional<uint32_t> declared_stream_;
  std::unordered_set<uint32_t> declared_streams_;
  uint64_t foreign_frames_ = 0;
  // The current failure was one session's own (fail_session): reported in
  // its events, and not the process's exit. An engine failure that follows
  // it (fail_engine) ends the containment: the exit reports the engine.
  bool failure_contained_ = false;
  // When the frame now pending was taken, and whether its hold was declared.
  // Input held longer than kHeldInputReport is back-pressure that reaches
  // the host's queue; it is said on AII_VOICE_BACKPRESSURE, with its end, so
  // a later declared gap can be traced to the stall (or ruled out).
  Clock::time_point pending_since_{};
  bool held_reported_ = false;
  static constexpr auto kHeldInputReport = std::chrono::milliseconds(1000);
  // A declared gap longer than this is not a glitch but a broken stream.
  static constexpr uint64_t kMaxGapSamples = 16000ull * 30;
  static constexpr uint64_t kGapChunkSamples = 4096;
  bool waiting_settings_ = false, pending_audio_ = false, abort_ = false,
       quit_ = false;
  Clock::time_point opening_deadline_, closing_deadline_, exit_deadline_;
  uint64_t drain_recognized_ = 0;
  // The sequence of the last event taken from the core. The core numbers its
  // events from one; the session ends only when the core has retired AND the
  // last event it numbered has been taken (pump).
  uint64_t core_taken_ = 0;
  std::optional<Frame> input_pending_;
  std::optional<Output> active_output_;
  AudioScratch<> audio_scratch_;
  Json processing_ = null(), effective_ = object();
  // The session's correction list, pinned with its settings. corrections_state_
  // is null unless the settings reply carried a list, so a host that sends
  // none sees exactly the readback it saw before.
  aii::voice::Corrections corrections_;
  Json corrections_state_ = null();
  aii_voice_snapshot snapshot_{};
  aii_voice_error error_{};

  // A graceful drain waits for finite, already-admitted work. Only measured
  // progress renews this inactivity bound; control traffic/status/duplicate
  // receipts cannot. Abort and capture preparation retain their own deadlines.
  void advance_drain() {
    if (lifecycle_ == "draining" && !abort_ && session_)
      closing_deadline_ = Clock::now() + std::chrono::seconds(15);
  }
  // The host can read PCM/END before the audio writer publishes that write's
  // Ack. A report only the active write could account for waits for its Ack;
  // nothing here credits unwritten audio or delays the control owner.
  bool unacknowledged(const Generation &g, uint64_t n, bool terminal) const {
    if (!pending_audio_ || active_output_->g.get() != &g || n < g.rendered)
      return false;
    const bool ahead = n > g.delivered || (terminal && !g.fenced && !g.ended);
    return ahead && n <= g.delivered + active_output_->pcm.size() &&
           (!terminal || g.fenced || g.ended || active_output_->audio.end);
  }
  // Admit or refuse held reports, in arrival order, once that Ack is counted.
  void reconcile() {
    auto reports = std::move(unreconciled_);
    unreconciled_.clear();
    for (auto &report : reports)
      answer(report.first, [&] {
        if (auto r = admit(report.first, "speech.session.playback_report", report.second.get()))
          reply(report.first, std::move(r));
      });
  }

  void fault_transport(const std::string &s) {
    std::lock_guard<std::mutex> l(mutex_);
    if (transport_fault_.empty())
      transport_fault_ = s;
    changed_.notify_all();
  }
  void send(Json j) {
    auto s = encode(j) + '\n';
    std::lock_guard<std::mutex> l(mutex_);
    if (wire_out_.size() >= 128 || wire_bytes_ + s.size() > 1024 * 1024)
      throw std::runtime_error("private output queue full");
    wire_bytes_ += s.size();
    wire_out_.push_back(std::move(s));
    changed_.notify_all();
  }
  uint64_t emit(const char *type, Json data = object()) {
    if (!failure_.empty() && std::string(type) != "failure")
      return 0;
    put(data, "type", string(type));
    put(data, "session_id", string(sid_));
    put(data, "sequence", number(++sequence_));
    put(data, "id", string(sid_ + ":" + std::to_string(sequence_)));
    put(data, "observed_monotonic_ns",
        number(monotonic_ns() % 9007199254740992ULL));
    auto message = object();
    put(message, "event", std::move(data));
    send(std::move(message));
    return sequence_;
  }
  void reply(uint64_t id, Json data) {
    auto message = object();
    put(message, "id", number(id));
    put(message, "result", std::move(data));
    send(std::move(message));
  }
  void refuse(uint64_t id, const char *reason) {
    auto message = object();
    put(message, "id", number(id));
    put(message, "error", string(reason));
    send(std::move(message));
  }
  template <class F> void answer(uint64_t id, F f) {
    try {
      f();
    } catch (const Refused &e) {
      refuse(id, e.what());
    } catch (const std::exception &e) {
      fail_engine(e.what());
      refuse(id, e.what());
    }
  }
  static Json accepted() {
    auto j = object();
    put(j, "accepted", boolean(true));
    return j;
  }
  template <class F> void launch(F f) {
    ++live_threads_;
    try {
      threads_.emplace_back([this, f = std::move(f)] {
        struct Retire {
          std::atomic<unsigned> &n;
          ~Retire() { --n; }
        } retire{live_threads_};
        try {
          f();
        } catch (const std::exception &e) {
          fault_transport(e.what());
        } catch (...) {
          fault_transport("unknown native I/O failure");
        }
      });
    } catch (...) {
      --live_threads_;
      throw;
    }
  }
  void start_threads() {
    launch([&] {
      try {
        std::string s;
        while (controls_.line(s, readers_stop_)) {
          std::unique_lock<std::mutex> l(mutex_);
          changed_.wait(
              l, [&] { return readers_stop_ || controls_in_.size() < 64; });
          if (readers_stop_)
            return;
          controls_in_.push_back(s);
          changed_.notify_all();
        }
        std::lock_guard<std::mutex> l(mutex_);
        eof_ = true;
        changed_.notify_all();
      } catch (const std::exception &e) {
        if (!readers_stop_)
          fault_transport(e.what());
      }
    });
    launch([&] {
      try {
        for (;;) {
          unsigned char h[28];
          if (!input_.exact(h, sizeof h, readers_stop_, true)) {
            std::lock_guard<std::mutex> l(mutex_);
            audio_eof_ = true;
            changed_.notify_all();
            return;
          }
          if (std::memcmp(h, "AUD1", 4) || h[5] || h[6] || h[7] || h[4] < 1 ||
              h[4] > 3)
            throw std::runtime_error("invalid AUD1 header");
          Frame f;
          f.kind = h[4];
          f.stream = uint32_t(be(h + 8, 4));
          f.seq = uint32_t(be(h + 12, 4));
          f.start = be(h + 16, 8);
          const auto bytes = be(h + 24, 4);
          if (bytes > 65536 || bytes % 2 || (f.kind != 1 && bytes) ||
              f.start > uint64_t(INT64_MAX))
            throw std::runtime_error("invalid AUD1 span");
          std::vector<unsigned char> raw(bytes);
          input_.exact(raw.data(), raw.size(), readers_stop_);
          f.pcm.resize(bytes / 2);
          for (size_t i = 0; i < f.pcm.size(); ++i) {
            const uint16_t u =
                uint16_t(raw[2 * i]) | (uint16_t(raw[2 * i + 1]) << 8);
            f.pcm[i] = float(int16_t(u)) / 32768.f;
          }
          std::unique_lock<std::mutex> l(mutex_);
          changed_.wait(l, [&] {
            return readers_stop_ || (audio_in_.size() < 64 &&
                                     audio_samples_ + f.pcm.size() <= 32768);
          });
          if (readers_stop_)
            return;
          audio_samples_ += f.pcm.size();
          audio_in_.push_back(std::move(f));
          changed_.notify_all();
        }
      } catch (const std::exception &e) {
        if (!readers_stop_)
          fault_transport(e.what());
      }
    });
    launch([&] {
      try {
        for (;;) {
          std::string s;
          {
            std::unique_lock<std::mutex> l(mutex_);
            changed_.wait(l, [&] { return settled_ || !wire_out_.empty(); });
            if (wire_out_.empty())
              return;
            s = std::move(wire_out_.front());
            wire_out_.pop_front();
            wire_bytes_ -= s.size();
          }
          wire_.write(s.data(), s.size(), io_stop_);
        }
      } catch (const std::exception &e) {
        fault_transport(e.what());
      }
    });
    launch([&] {
      for (;;) {
        Output task;
        {
          std::unique_lock<std::mutex> l(mutex_);
          changed_.wait(l,
                        [&] { return settled_ || output_task_.has_value(); });
          if (!output_task_)
            return;
          task = std::move(*output_task_);
          output_task_.reset();
        }
        Ack ack;
        try {
          for (size_t offset = 0; offset < task.pcm.size() || task.audio.end;) {
            if (!task.audio.end && task.g->fenced)
              break;
            const size_t n =
                task.audio.end
                    ? 0
                    : std::min(size_t(32768), task.pcm.size() - offset);
            std::vector<unsigned char> raw(28 + n * 2, 0);
            std::memcpy(raw.data(), "AUD1", 4);
            raw[4] = task.audio.end ? 3 : 1;
            be(raw.data() + 8, task.g->stream, 4);
            be(raw.data() + 12, uint64_t(task.seq) + ack.frames, 4);
            be(raw.data() + 16, task.start + ack.samples, 8);
            be(raw.data() + 24, n * 2, 4);
            for (size_t i = 0; i < n; ++i) {
              const auto v = int16_t(std::nearbyint(
                  std::clamp(task.pcm[offset + i], -1.f, 32767.f / 32768) *
                  32768));
              raw[28 + 2 * i] = uint8_t(v);
              raw[29 + 2 * i] = uint8_t(uint16_t(v) >> 8);
            }
            output_.write(raw.data(), raw.size(), io_stop_);
            ack.samples += n;
            ++ack.frames;
            offset += n;
            if (task.audio.end) {
              ack.end = true;
              break;
            }
          }
        } catch (const UnframedWrite &e) {
          // Part of a frame may be on the audio pipe, or the host stopped
          // reading past the deadline: no later byte there could be framed.
          // Retire the worker by the transport fault the main loop raises
          // when it sees the deadline first; the Ack carries the same cause.
          ack.error = e.what();
          fault_transport(e.what());
        } catch (const std::exception &e) {
          ack.error = e.what(); // refused before its first byte: only whole frames are on the pipe
        }
#ifdef AII_AUDIO_ACK_TEST_HOOK
        before_audio_ack(task.audio.end, !ack.error.empty());
#endif
        {
          std::lock_guard<std::mutex> l(mutex_);
          ack_ = std::move(ack);
        }
        changed_.notify_all();
      }
    });
  }
  // A failure that belongs to one session — its own contract refused, or its
  // host did not answer in time — ends that session; the engine is healthy,
  // so the process's exit does not report it (the session already did).
  // The first cause stands: a later fault of the same session adds nothing.
  void fail_session(const std::string &reason) {
    if (!failure_.empty())
      return;
    fail(reason);
    failure_contained_ = true;
  }
  // An engine failure is the process's own, whatever a session reported
  // before it: a native model or session error, a core error in a request
  // or in settings, a broken control channel. After a session's contained
  // failure it is still said, and the exit reports it. The first cause stays
  // the session's, in its events and its status.
  void fail_engine(const std::string &reason) {
    if (!failure_.empty() && failure_contained_) {
      failure_contained_ = false;
      say_failure(reason);
      return;
    }
    fail(reason);
  }
  // JSON escaping prevents log injection; no PCM, transcript or profile
  // document belongs in this lifecycle diagnostic.
  void say_failure(const std::string &reason) {
    auto diagnostic=object();
    put(diagnostic,"component",string("voice-worker"));
    put(diagnostic,"event",string("failure"));
    put(diagnostic,"session_id",string(sid_));
    put(diagnostic,"reason",string(reason.substr(0,1024)));
    std::cerr<<"AII_VOICE_FAILURE "<<encode(diagnostic)<<'\n';
  }
  void fail(const std::string &reason) {
    if (!failure_.empty())
      return;
    failure_ = reason;
    // Persist the first cause before cleanup or carrier EOF can hide it.
    say_failure(reason);
    capture_cancelled_=true;
    uid_snapshot_.cancel();
    if (lifecycle_ == "closed")
      return;
    lifecycle_ = "draining";
    abort_ = true;
    waiting_settings_ = false;
    closing_deadline_ = Clock::now() + std::chrono::seconds(5);
    for (auto &g : generations_)
      g.second->fenced = true;
    if (session_)
      core(aii_voice_close(session_, 1, &error_), error_);
  }
  bool session_retired() const {
    return lifecycle_ == "closed" || lifecycle_ == "failed";
  }
  Json status() {
    auto r = object();
    char execution[16385]{};size_t required=0;
    core(aii_voice_models_execution(models_,execution,sizeof execution,&required,&error_),error_);
    put(r,"model_execution",parse(execution));
    put(r, "session_id", string(sid_));
    put(r, "state_sequence", number(sequence_));
    put(r, "attributions", attributions_.snapshot());
    put(r, "lifecycle", string(lifecycle_));
    put(r, "reason", failure_.empty() ? null() : string(failure_));
    put(r, "operator_settings", clone(effective_.get()));
    if(!cJSON_IsNull(corrections_state_.get()))put(r,"corrections",clone(corrections_state_.get()));
    put(r,"purpose",string(capture_?"enrollment_capture":"conversation"));
    put(r,"enrollment_capture",clone(capture_result_.get()));
    auto input = object();
    if (cJSON_IsNull(processing_.get()))
      put(input, "processing", null());
    else {
      auto p = object();
      put(p, "source", string("browser_reported"));
      put(p, "reported", clone(processing_.get()));
      put(p, "echo_cancellation_verified", boolean(false));
      put(p, "engine_echo_cancellation", boolean(reference_input_));
#ifdef AII_WITH_ECHO
      if(echo_) {
        const auto state=echo_->status();
        put(p,"reference_source",string("browser_output_graph_same_clock"));
        put(p,"reference_generation",number(state.generation));
        put(p,"processed_frames",number(state.processed_frames));
        put(p,"alignment_buffer_samples",number(160));
      }
#endif
      put(input, "processing", std::move(p));
    }
    put(input, "state",
        string(!input_enabled_        ? "absent"
               : input_final_sequence_ ? "finished"
               : snapshot_.cutoff_set ? "finishing"
                                      : "accepting"));
    put(input, "admitted_end_sample",
        snapshot_.cutoff_set ? number(snapshot_.cutoff) : null());
    put(input, "received_end_sample", number(input_received_));
    put(input, "stream", declared_stream_ ? number(uint64_t(*declared_stream_)) : null());
    put(input, "foreign_frames", number(foreign_frames_));
    put(input, "processed_end_sample", number(snapshot_.recognized));
    put(r, "input", std::move(input));
    auto rec = object();
    if(!input_enabled_) put(rec,"state",string("inactive"));
    put(rec, "utterance_open", boolean(snapshot_.recognition_active));
    put(rec, "finalization_pending",
        boolean(snapshot_.cutoff_set && !input_final_sequence_));
    put(r, "recognition", std::move(rec));
    auto complete = null();
    if (input_final_sequence_) {
      complete = object();
      put(complete, "stream_id", string(input_handle_));
      put(complete, "end_sample", number(snapshot_.cutoff));
      put(complete, "processed_end_sample", number(snapshot_.recognized));
      put(complete, "sequence", number(input_final_sequence_));
      if(capture_limit_reached_)put(complete,"reason",string("capture_limit"));
    }
    put(r, "input_completion", std::move(complete));
    auto synth = object();
    put(synth, "synthesis_id",
        string(current_name_));
    put(synth, "state",
        string(snapshot_.synthesizing ? "running"
               : current_             ? "finished"
                                      : "idle"));
    put(r, "synthesis", std::move(synth));
    uint64_t delivered = settled_delivered_, rendered = settled_rendered_, queued = 0,
             discarded = settled_delivered_-settled_rendered_;
    bool unresolved = false;
    for (const auto &item : generations_) {
      const auto &g = *item.second;
      delivered += g.delivered;
      rendered += g.rendered;
      if (g.receipt)
        discarded += g.delivered - g.rendered;
      else {
        unresolved = true;
        queued += g.delivered - g.rendered;
      }
    }
    auto playback = object();
    put(playback, "synthesis_id",
        string(current_name_));
    put(playback, "state", string(unresolved ? "unobserved" : "idle"));
    put(playback, "queued_samples", number(queued));
    put(playback, "delivered_samples", number(delivered));
    put(playback, "rendered_samples", number(rendered));
    put(playback, "discarded_samples", number(discarded));
    put(playback, "sample_rate", number(24000));
    put(playback, "evidence",
        string("client_reports_not_acoustic_measurements"));
    put(r, "playback", std::move(playback));
    auto book=object();put(book,"unresolved_generations",number(generations_.size()));
    put(book,"identity_fences",number(synthesis_identities_.size()));
    put(book,"session_fences",number(used_sessions_.size()));
    put(r,"bookkeeping",std::move(book));
    return r;
  }
  void validate_processing(const cJSON *p) {
    if (!p || cJSON_IsNull(p)) {
      processing_ = null();
      return;
    }
    require(cJSON_IsObject(p), "capture processing object required");
    for (auto *f = p->child; f; f = f->next) {
      std::string key = f->string;
      if (key == "tested")
        require(cJSON_IsFalse(f),
                "capture report cannot claim acoustic testing");
      else if (key == "sample_rate") {
        if (!cJSON_IsNull(f))
          require(integer(f, 384000) >= 8000, "capture rate invalid");
      } else {
        require(key == "echo_cancellation" || key == "noise_suppression" ||
                    key == "auto_gain_control",
                "unknown capture diagnostic");
        require(cJSON_IsNull(f) || cJSON_IsBool(f), "capture boolean required");
      }
    }
    processing_ = clone(p);
  }
  Json waveform_result() {
    auto data=object();put(data,"state",string(waveform_state_));
    if(!waveform_id_.empty())put(data,"recording_id",string(waveform_id_));
    put(data,"buffered_audio_available",boolean(recent_waveforms_.available()));
    put(data,"samples",number(waveform_saved_samples_));
    put(data,"sample_rate",number(aii::voice::RecentWaveforms::rate));
    put(data,"channels",number(1));put(data,"format",string("wav_pcm16le"));
    put(data,"speaker_separated",boolean(false));
    if(!waveform_session_.empty()) {
      put(data,"source_session_id",string(waveform_session_));
      put(data,"source_start_sample",number(waveform_start_));
      put(data,"source_end_sample",number(waveform_end_));
    }
    if(waveform_state_=="saved") {
      put(data,"private_path",string("recordings/"+waveform_id_+".wav"));
      put(data,"sha256",string(waveform_sha_));
    }
    if(!waveform_reason_.empty())put(data,"reason",string(waveform_reason_));
    auto result=object();put(result,"status",string("succeeded"));
    put(result,"operation_result",std::move(data));return result;
  }
  bool waveform_control(uint64_t request,const std::string& op,const cJSON* a) {
    if(op!="recording.record"&&op!="recording.status")return false;
    require(cJSON_IsObject(a),"recording arguments object required");
    if(op=="recording.record") {
      require(!waveform_publish_.valid() && (lifecycle_=="open"||session_retired()) &&
              !enrollment_.valid() && !capturing_.valid(),
              "prior recording or storage operation unresolved");
      require(recent_waveforms_.available(),"no buffered microphone audio to record");
      // A refused or timed-out private write must not consume the only audio.
      if(session_retired())uid_snapshot_.begin("recording-"+std::to_string(request));
      aii::voice::RecentWaveforms::Recording recording;
      try { recording=recent_waveforms_.latest(); }
      catch(const std::invalid_argument& e) { throw Refused(e.what()); }
      auto wav=aii::voice::RecentWaveforms::wav(recording.pcm);
      waveform_saved_samples_=recording.pcm.size();
      aii::voice::RecentWaveforms::wipe(recording.pcm);
      waveform_session_=recording.session;
      waveform_start_=recording.start;waveform_end_=recording.end;
      waveform_id_=picosha2::hash256_hex_string(recording.session+std::string(1,'\0')+
          std::to_string(recording.end)+std::string(1,'\0')+std::to_string(request));
      waveform_sha_.clear();waveform_reason_.clear();waveform_state_="publishing";
      waveform_request_=request;
      const auto id=waveform_id_;const auto sha=picosha2::hash256_hex_string(wav);
      waveform_publish_=std::async(std::launch::async,[this,id,sha,wav=std::move(wav)]() mutable {
        auto wipe=[&] {volatile char* p=wav.data();for(size_t i=0;i<wav.size();++i)p[i]=0;};
        try {
          auto receipt=uid_snapshot_.publish(wav,"",true,id,aii::voice::SnapshotBridge::Store::Waveform,id);
          require(flag(field(receipt.get(),"durable"))&&flag(field(receipt.get(),"readback_verified")),
                  "waveform private publication not durable and verified");
          auto result=object();put(result,"sha256",string(sha));wipe();return result;
        }catch(...){wipe();throw;}
      });
      return true; // The reply is the saved-file receipt, not an interim state.
    }
    reply(request,waveform_result());return true;
  }
  Json open(const cJSON *a) {
    require(session_retired() && !session_ && !opening_.valid()&&!enrollment_.valid()&&!capturing_.valid()&&!waveform_publish_.valid(),
            "prior resources not released");
    std::optional<aii::voice::CaptureInput> capture;
    if(const auto* requested=field(a,"enrollment_capture")) {
      require(uid_policies_&&uid_policies_->current().policy.minimum_enrollment_samples==1&&readiness_.models_loaded==5,
          "guided capture needs an explicitly bound single-recording policy");
      capture.emplace(requested);
    }
    const auto id = str(field(a, "session_id"), 128);
    require(!used_sessions_.count(identity_digest(id)) && session_epoch_<9007199254740991ULL,
            "session ID reuse/epoch exhausted");
    str(field(a, "output_handle"));
    const auto *audio = field(a, "audio");
    require(cJSON_IsObject(audio), "audio object required");
    // The shared host/SDK topology contract defaults an omitted format to
    // s16le. A present null, wrong type or other encoding is still refused.
    if(const auto* format=field(audio,"format"))
      require(str(format)=="s16le","only s16le audio is supported");
    const auto* source=field(audio,"input");
    require(source!=nullptr,"explicit input format or null required");
    const bool input_enabled=!cJSON_IsNull(source);
    require(input_enabled || (!field(a,"input_handle") && !capture),
            "absent input cannot carry an input handle or enrollment capture");
    const auto handle=input_enabled ? str(field(a,"input_handle")) : std::string{};
    std::optional<uint32_t> declared;
    if(const auto* stream=input_enabled ? field(source,"stream") : nullptr) {
      declared=uint32_t(integer(stream,UINT32_MAX));
      require(!declared_streams_.count(*declared),"input stream number already served an earlier session");
    }
    for (const char *name : {"input", "output"}) {
      if(!input_enabled && std::string(name)=="input") continue;
      const auto *f = field(audio, name);
      require(cJSON_IsObject(f) && integer(field(f, "rate"), 192000) >= 8000,
              "audio rate invalid");
      const auto channels = integer(field(f, "channels"), 2);
      require(channels >= 1, "audio channels invalid");
    }
    validate_processing(field(field(audio, "input"), "processing"));
    const auto* roles=field(source,"channel_roles");
    bool reference=false;
    if(roles) {
      require(input_enabled && !capture && cJSON_IsArray(roles) && cJSON_GetArraySize(roles)==2 &&
          integer(field(source,"channels"),2)==2 &&
          str(cJSON_GetArrayItem(roles,0))=="capture" && str(cJSON_GetArrayItem(roles,1))=="playback_reference",
          "unsupported input channel roles");
#ifdef AII_WITH_ECHO
      reference=true;
#else
      throw Refused("native playback-reference processing not included in this runtime");
#endif
    }
#ifdef AII_WITH_ECHO
    // Build before admitting open: a failed DSP allocation leaves no session.
    auto next_echo=reference ? std::make_unique<aii::voice::EchoInput>(session_epoch_+1) : nullptr;
    echo_=std::move(next_echo);echo_cutoff_.reset();echo_tail_.clear();
#endif
    reference_input_=reference;
    sid_ = id;
    uid_snapshot_.begin(sid_);
    recent_waveforms_.begin(sid_);
    attributions_.begin(sid_);
    enrollment_finals_.clear();
    input_handle_ = handle;
    input_enabled_ = input_enabled;
    used_sessions_.insert(identity_digest(id));++session_epoch_;
    declared_stream_=declared;foreign_frames_=0;
    if(declared)declared_streams_.insert(*declared);
    settled_delivered_=settled_rendered_=0;current_name_.clear();
    sequence_ = 0;
    failure_.clear();
    failure_contained_ = false;
    abort_ = false;
    current_ = 0;
    generations_.clear();
    if (input_started_) {
      retired_streams_.push_back(input_stream_);
      if (retired_streams_.size() > 16)
        retired_streams_.pop_front();
    }
    input_started_ = false;
    end_seen_ = false;
    input_limit_ = 0;
    capture_limit_reached_ = false;
    gap_open_ = false;
    gap_target_ = 0;
    input_received_ = input_final_sequence_ = 0;
    core_taken_ = 0;
    snapshot_ = {};
    capture_=std::move(capture);capture_result_=null();capture_cancelled_=false;
    effective_ = object();
    corrections_ = {}; corrections_state_ = null();
    early_finish_.reset();
    lifecycle_ = "opening";
    waiting_settings_ = !capture_;
    opening_deadline_ = Clock::now() + std::chrono::seconds(2);
    emit("session_start");
    if(capture_) {
      // Enrollment audio is never opened as a recognizer/session and never
      // reaches conversation. Preparation borrows the existing model owner
      // only after the complete input boundary has arrived.
      lifecycle_="open";
      auto e=object(),models=object();
      put(models,"backend",string(backend_name(readiness_)));
      put(models,"purpose",string("enrollment_capture"));put(e,"models",std::move(models));
      emit("session_ready",std::move(e));
    }else {
    auto query = object(), message = object();
    put(query, "id", number(++settings_id_));
    issued_settings_[settings_id_] = sid_;
    if (issued_settings_.size() > 32)
      issued_settings_.erase(issued_settings_.begin());
    put(query, "session_id", string(sid_));
    put(message, "settings_request", std::move(query));
    send(std::move(message));
    }
    auto r = accepted(), formats = object(), in = object(), out = object();
    put(in, "rate", number(16000));
    put(in, "channels", number(reference_input_?2:1));
    if(reference_input_)put(in,"channel_roles",clone(roles));
    put(out, "rate", number(24000));
    put(out, "channels", number(1));
    put(formats, "input", input_enabled_ ? std::move(in) : null());
    put(formats, "output", std::move(out));
    put(r, "session_id", string(sid_));
    put(r, "state", string(lifecycle_));
    put(r,"purpose",string(capture_?"enrollment_capture":"conversation"));
    put(r, "audio", std::move(formats));
    return r;
  }
  void settings(const cJSON *p) {
    require(cJSON_IsObject(p), "settings reply object required");
    const auto key = integer(field(p, "id"));
    const auto who = str(field(p, "session_id"));
    require(issued_settings_.count(key) && issued_settings_.at(key) == who,
            "foreign settings reply");
    if (key != settings_id_ || who != sid_)
      return;
    if (!waiting_settings_)
      return; // retired open cannot configure a successor
    require(!field(p, "error") && cJSON_IsObject(field(p, "values")),
            "host settings unavailable");
    const auto *values = field(p, "values");
    const auto config=OperatorSettings::read(values);
    input_limit_=aii::voice::capture_samples(config.capture_limit_minutes);
    effective_=config.effective();
    // What a rule says was meant is also what the recognizer should prefer
    // to write where the sound is close: every meant is handed to it as a
    // term, for this session. It keeps those it can spell as short phrases
    // and passes over the rest; the readback says how many it kept.
    std::vector<const char*> preferred;
    // A list that cannot be held does not take speech away: the session runs
    // uncorrected and says why, in its readback and in the lifecycle log.
    if(const auto* stored=field(p,"corrections")) {
      corrections_state_=object();
      try {
        auto document=aii::voice::wire::read_corrections(stored);
        put(corrections_state_,"revision",number(document.revision));
        put(corrections_state_,"rules",number(document.list.rules().size()));
        corrections_=std::move(document.list);
        for(const auto& rule:corrections_.rules())preferred.push_back(rule.meant.c_str());
      } catch(const Refused& e) {
        corrections_={};
        put(corrections_state_,"unreadable",string(std::string(e.what()).substr(0,256)));
        auto diagnostic=object();
        put(diagnostic,"component",string("voice-worker"));put(diagnostic,"event",string("corrections_unreadable"));
        put(diagnostic,"session_id",string(sid_));put(diagnostic,"reason",string(std::string(e.what()).substr(0,256)));
        std::cerr<<"AII_VOICE_CORRECTIONS "<<encode(diagnostic)<<'\n';
      }
    }
    // Every session sets the recognizer's terms, an empty list included, so
    // one session's names never reach the next.
    uint32_t kept=0;
    core(aii_voice_models_prefer(models_,preferred.empty()?nullptr:preferred.data(),uint32_t(preferred.size()),&kept,&error_),error_);
    if(cJSON_IsObject(corrections_state_.get())&&!field(corrections_state_.get(),"unreadable"))put(corrections_state_,"preferred",number(kept));
    waiting_settings_ = false;
    opening_ = std::async(std::launch::async, [this, config, input_enabled=input_enabled_] {
      aii_voice_error e{};
      aii_voice_session *s = nullptr;
      const auto speech=config.speech();
      const aii_voice_open_options options{&config.control,&speech,config.capture_limit_minutes,uint8_t(input_enabled)};
      core(aii_voice_open_session(models_, &options, &s, &e), e);
      return s;
    });
  }
  bool enroll(uint64_t request,const std::string& op,const cJSON* a) {
    const bool buckets=op=="speaker.buckets"||op=="speaker.associate"||op=="speaker.forget"||op=="speaker.link";
    if(!buckets&&op!="speaker.enroll"&&op!="speaker.list"&&op!="speaker.remove"&&op!="speaker.reset"&&op!="speaker.discard_capture"&&op!="speaker.upgrade_policy")return false;
    require(cJSON_IsObject(a)&&uid_policies_.has_value()&&readiness_.models_loaded==5,"native UID operation unavailable");
    require(op!="speaker.upgrade_policy"||session_retired(),"close speech before confirmed enrollment policy upgrade");
    const bool recovery=field(a,"recovery")!=nullptr;
    std::string recover_profile,recover_captures,recover_registry;
    if(recovery){
      require(op=="speaker.reset"&&session_retired()&&cJSON_IsObject(field(a,"recovery")),"confirmed recovery requires closed speech and speaker.reset");
      const auto* r=field(a,"recovery");
      // The registry digest is present exactly when speaker.list reported it.
      const auto* registry=field(r,"speaker_registry_sha256");
      require(cJSON_GetArraySize(r)==(registry?3:2),"recovery requires exactly the observed digests");
      recover_profile=str(field(r,"enrollment_sha256"),64);recover_captures=str(field(r,"captures_sha256"),64);
      if(registry)recover_registry=str(registry,64);
    }
    const bool guided=field(a,"capture_id")!=nullptr;
    const bool captures=op=="speaker.enroll"&&!guided;
    const auto capture_id=guided?str(field(a,"capture_id"),64):"";
    require(!guided||(capture_id.size()==64&&capture_id.find_first_not_of("0123456789abcdef")==std::string::npos&&
        !field(a,"finals")&&(op=="speaker.enroll"||op=="speaker.discard_capture")),"exact capture_id without live finals required");
    require(op!="speaker.discard_capture"||guided,"capture_id required");
    require(!field(a,"session_id")||str(field(a,"session_id"),128)==sid_,"session_id differs; call speaker.list without a session_id to inspect current state");
    require(!enrollment_.valid()&&!capturing_.valid()&&!waveform_publish_.valid()&&(!capture_||session_retired())&&
        (lifecycle_=="open"||session_retired()),"speaker management waits for capture close, opening, closing or enrollment");
    require(!captures||(field(a,"session_id")&&lifecycle_=="open"&&session_),"enrollment requires live finalized recordings; call speaker.list for session_open and eligible_final_sequences");
    const bool mutates=op!="speaker.list"&&op!="speaker.buckets";std::string act;
    if(mutates) {
      const auto* stamp=field(a,"_host_operator_act");
      require(cJSON_IsObject(stamp),"operator confirmation required");
      act=str(field(stamp,"id"),128);str(field(stamp,"confirmed_at"),64);
      // A repeated one-use confirmation refuses this operation, not the
      // resident speech session. Allocation/runtime failures still fault it.
      try { enrollment_acts_.check(act); }
      catch (const std::invalid_argument& e) { throw Refused(e.what()); }
    }
    if(buckets) {
      require(bool(registry_),"speaker registry unavailable");
      std::string uuid,label,external,target;uint64_t revision=0;
      if(op!="speaker.buckets") {
        uuid=str(field(a,"speaker_uuid"),36);
        if(op=="speaker.link")target=str(field(a,"target_uuid"),36);
        if(op=="speaker.associate")label=bounded_text(field(a,"display_label"),512);
        if(field(a,"external_id"))external=bounded_text(field(a,"external_id"),512);
        const auto raw=str(field(a,"registry_revision"),16);
        require(!raw.empty()&&raw.find_first_not_of("0123456789")==std::string::npos &&
          (raw=="0"||raw[0]!='0'),"canonical registry revision required");
        revision=std::stoull(raw);require(revision<=9007199254740991ULL,"registry revision exceeds bound");
      }
      if(session_retired())uid_snapshot_.begin("uid-management-"+std::to_string(request));
      if(mutates)enrollment_acts_.consume(act);
      enrollment_request_=request;
      enrollment_=std::async(std::launch::async,[this,op,revision,uuid,label,external,target] {
        auto data=op=="speaker.forget"?registry_->forget(revision,uuid):
            op=="speaker.link"?registry_->link(revision,uuid,target):
            op=="speaker.associate"?registry_->associate(revision,uuid,label,external):registry_->list();
        put(data,"session_open",boolean(false));
        auto result=object();put(result,"status",string("succeeded"));put(result,"operation_result",std::move(data));return result;
      });
      return true;
    }
    const auto speaker=op=="speaker.enroll"||op=="speaker.remove"?str(field(a,"speaker_id"),128):"";
    const auto label=op=="speaker.enroll"?str(field(a,"label"),512):"";
    std::vector<uint64_t> selected;
    if(captures) {
      const auto* finals=field(a,"finals");
      require(cJSON_IsArray(finals)&&cJSON_GetArraySize(finals)>0&&cJSON_GetArraySize(finals)<=8,"select one to eight session finals");
      std::set<uint64_t> seen;
      for(auto* f=finals->child;f;f=f->next) {
        auto n=integer(f);require(seen.insert(n).second&&enrollment_finals_.count(n),"selected final foreign, repeated or no longer retained");
        selected.push_back(enrollment_finals_.at(n));
      }
    }
    // Closed speech does not close the plugin's ordinary management lane.
    // Reuse the same brokered snapshot owner; no second profile store and no
    // model session or microphone is opened just to manage existing speakers.
    if(session_retired())uid_snapshot_.begin("uid-management-"+std::to_string(request));
    if(mutates)enrollment_acts_.consume(act);
    // "auto" may legitimately recur under the host's standing confirmation;
    // each admitted request still gets its own staging identity.
    const auto upload=picosha2::hash256_hex_string(sid_+std::string(1,'\0')+act+std::string(1,'\0')+std::to_string(request));
    const auto policies=*uid_policies_;const auto policy=policies.current();auto* session=session_;enrollment_request_=request;
    const auto session_id=sid_;const auto final_map=enrollment_finals_;
    enrollment_=std::async(std::launch::async,[this,op,mutates,guided,capture_id,speaker,label,selected,upload,policies,policy,session,session_id,final_map,recovery,recover_profile,recover_captures,recover_registry] {
      if(recovery){
        auto data=aii::voice::recover_uid(uid_snapshot_,policies,recover_profile,recover_captures,upload,recover_registry);
        put(data,"session_id",string(session_id));put(data,"session_open",boolean(false));put(data,"used_for_permissions",boolean(false));
        auto result=object();put(result,"status",string("succeeded"));put(result,"operation_result",std::move(data));return result;
      }
      auto inspection=object();
      if(op=="speaker.list"){
        auto state=aii::voice::inspect_uid(uid_snapshot_,policies);inspection=state.report();
        if(state.needs_recovery()){
          auto data=object();put(data,"recovery",std::move(inspection));
          put(data,"session_id",string(session_id));put(data,"session_open",boolean(session!=nullptr));put(data,"used_for_permissions",boolean(false));
          auto result=object();put(result,"status",string("succeeded"));put(result,"operation_result",std::move(data));return result;
        }
      }
      if(guided) {
        if(op=="speaker.enroll") {
          bool absent=false;const auto current=uid_snapshot_.read(&absent);
          if(!absent) {
            (void)policies.read(current);
            require(policies.resolve(current).policy.fingerprint==policy.policy.fingerprint,
                "speaker.upgrade_policy requires operator confirmation before guided enrollment; call speaker.list");
          }
        }
        aii::voice::CaptureEnrollment store(uid_snapshot_,policy);
        auto data=object();put(data,"capture_id",string(capture_id));
        put(data,"session_id",string(session_id));put(data,"session_open",boolean(session!=nullptr));
        put(data,"used_for_permissions",boolean(false));bool durable=false;std::string detail;
        if(op=="speaker.enroll") {
          auto confirmed=store.confirm(capture_id,speaker,label,upload);
          durable=confirmed.enrollment_durable;detail=confirmed.cleanup_detail;
          put(data,"speaker_id",string(speaker));put(data,"label",string(label));
          put(data,"revision",string(std::to_string(confirmed.revision)));
          put(data,"enrollment_durable",boolean(confirmed.enrollment_durable));
          put(data,"capture_retirement_durable",boolean(confirmed.capture_retirement_durable));
          put(data,"reconciled",boolean(confirmed.reconciled));
          put(data,"publication",std::move(confirmed.enrollment_publication));
          put(data,"capture_publication",std::move(confirmed.capture_publication));
        }else {
          auto publication=store.discard(capture_id,upload);
          durable=flag(field(publication.get(),"durable"))&&flag(field(publication.get(),"readback_verified"));
          put(data,"capture_retirement_durable",boolean(durable));put(data,"publication",std::move(publication));
          if(!durable)detail="Capture discard durability unresolved; reconcile before retrying.";
        }
        if(!detail.empty())put(data,"cleanup_detail",string(detail));
        auto result=object();put(result,"status",string(durable?"succeeded":"failed"));
        if(!detail.empty()) {
          put(result,"reason",string(detail));
          put(result,"detail",string(detail));
        }
        put(result,"operation_result",std::move(data));return result;
      }
      bool absent=false;auto current=uid_snapshot_.read(&absent);
      if(absent)current=aii::uid::write_snapshot({policy.policy,0,{}},policy);
      auto effective=policies.resolve(current);
      auto snapshot=aii::uid::read_snapshot(current,effective);std::string candidate;
      const bool upgrade_required=effective.policy.fingerprint!=policy.policy.fingerprint;
      if(op=="speaker.enroll") {
        std::string out(8u<<20,'\0');size_t n=0;aii_voice_error e{};
        core(aii_voice_enroll_selected(session,current.data(),current.size(),speaker.c_str(),label.c_str(),
          selected.data(),selected.size(),out.data(),out.size(),&n,&e),e);
        require(n>1&&n<=out.size(),"prepared enrollment extent invalid");out.resize(n-1);candidate=std::move(out);
      }else if(op=="speaker.remove")candidate=aii::uid::prepare_removal(current,effective,speaker).snapshot;
      else if(op=="speaker.reset")candidate=aii::uid::prepare_reset(current,effective).snapshot;
      else if(op=="speaker.upgrade_policy") {
        require(policies.previous().has_value(),"no enrollment policy upgrade bound by this runtime");
        // Same-policy retries re-publish identical bytes and verify durability;
        // merely reading an earlier uncertain write is not a successful commit.
        candidate=upgrade_required?aii::uid::prepare_guided_policy_transition(current,effective,policy).snapshot:current;
        effective=policy;
      }
      auto data=object();bool durable=true;
      if(mutates) {
        if(op=="speaker.enroll") {
        aii_voice_error e{};aii_voice_snapshot state{};core(aii_voice_status(session,&state,&e),e);
        require(!state.closing&&!state.aborted&&!state.retired,"session ended before enrollment publication");
        }
        auto published=uid_snapshot_.publish(candidate,absent?"":picosha2::hash256_hex_string(current),absent,upload);
        durable=flag(field(published.get(),"durable"))&&flag(field(published.get(),"readback_verified"));put(data,"publication",std::move(published));
        snapshot=aii::uid::read_snapshot(candidate,effective);absent=false;
        if(op=="speaker.upgrade_policy") {
          require(durable,"enrollment policy publication durability unresolved; speaker registry remains unchanged");
          require(bool(registry_),"speaker registry unavailable for confirmed policy upgrade");
          auto migrated=registry_->upgrade_policy();
          put(data,"speaker_registry_revision",clone(field(migrated.get(),"registry_revision")));
          put(data,"speaker_registry_policy_upgrade_required",boolean(false));
        }
      }
      auto rows=own(cJSON_CreateArray());
      for(const auto& s:snapshot.speakers) {
        auto row=object();put(row,"speaker_id",string(s.id));put(row,"label",string(s.label));
        put(row,"recordings",number(s.samples.size()));put(row,"ready",boolean(s.samples.size()>=effective.policy.minimum_enrollment_samples));
        require(cJSON_AddItemToArray(rows.get(),row.get()),"speaker list allocation failed");row.release();
      }
      put(data,"speakers",std::move(rows));put(data,"revision",string(std::to_string(snapshot.revision)));
      put(data,"enrollment_file_absent",boolean(absent));put(data,"used_for_permissions",boolean(false));
      put(data,"session_id",string(session_id));
      put(data,"session_open",boolean(session!=nullptr));
      put(data,"policy_sha256",string(effective.policy.fingerprint));
      put(data,"runtime_policy_sha256",string(policy.policy.fingerprint));
      put(data,"policy_upgrade_required",boolean(effective.policy.fingerprint!=policy.policy.fingerprint));
      if(op=="speaker.upgrade_policy")put(data,"reconciled",boolean(durable));
      if(!mutates) {
        put(data,"recovery",std::move(inspection));
        auto pending=own(cJSON_CreateArray());
        if(policy.policy.minimum_enrollment_samples==1) {
          aii::voice::CaptureEnrollment store(uid_snapshot_,policy);
          for(const auto& capture:store.list()) {
            auto row=object();put(row,"capture_id",string(capture.id));put(row,"created_ms",number(capture.created_ms));
            put(row,"samples",number(capture.samples));require(cJSON_AddItemToArray(pending.get(),row.get()),"capture list allocation failed");row.release();
          }
        }
        put(data,"pending_captures",std::move(pending));
        put(data,"guided_capture_available",boolean(policy.policy.minimum_enrollment_samples==1));
        uint64_t available[16];size_t n=0;aii_voice_error e{};
        if(session)core(aii_voice_enrollment_finals(session,available,16,&n,&e),e);
        const std::set<uint64_t> actual(available,available+n);auto finals=own(cJSON_CreateArray());
        for(const auto& row:final_map)if(actual.count(row.second)) {
          auto value=number(row.first);require(cJSON_AddItemToArray(finals.get(),value.get()),"final discovery allocation failed");value.release();
        }
        put(data,"eligible_final_sequences",std::move(finals));
        put(data,"minimum_recordings_per_speaker",number(effective.policy.minimum_enrollment_samples));
        put(data,"recording_retention_seconds",number(600));
      }
      auto result=object();put(result,"status",string(durable?"succeeded":"failed"));
      if(!durable) {
        const char* reason="Enrollment bytes read back, but publication durability is unknown; do not assume unchanged or repeat automatically.";
        put(result,"reason",string(reason));
        put(result,"detail",string(reason));
      }
      put(result,"operation_result",std::move(data));return result;
    });
    return true;
  }
  Json admit(uint64_t request, const std::string &op, const cJSON *a) {
    require(cJSON_IsObject(a), "arguments object required");
    if (op == "speech.session.open")
      return open(a);
    require(!sid_.empty() && str(field(a, "session_id")) == sid_,
            "stale session");
    if (op == "speech.session.status")
      return status();
    if (op == "speech.session.close") {
      const auto mode = str(field(a, "mode"));
      require(mode == "abort" || mode == "drain", "close mode required");
      require(lifecycle_ != "closed" && lifecycle_ != "failed" &&
                  (lifecycle_ != "draining" || mode == "abort"),
              "session cannot close in current state");
      require(mode == "abort" || ((session_||capture_) && (!input_enabled_ || snapshot_.cutoff_set)),
              "drain needs admitted Finish");
      if (mode == "abort") {
        capture_cancelled_=true;
        uid_snapshot_.cancel();
        abort_ = true;
        waiting_settings_ = false;
        for (auto &item : generations_)
          item.second->fenced = true;
      }
      lifecycle_ = "draining";
      closing_deadline_ =
          Clock::now() + std::chrono::seconds(mode == "abort" ? 5 : capture_ ? 45 : 15);
      drain_recognized_ = snapshot_.recognized;
      if (session_)
        core(aii_voice_close(session_, abort_, &error_), error_);
      auto r = accepted();
      put(r, "mode", string(mode));
      return r;
    }
    if(capture_) {
      require(lifecycle_=="open"||lifecycle_=="draining","capture session not ready");
      require(op=="speech.session.finish_input","enrollment capture never synthesizes or plays conversation");
      require(str(field(a,"stream_id"))==input_handle_,"foreign input handle");
      const auto end=integer(field(a,"end_sample"),480000);
      const bool first=!capture_->cutoff();capture_->finish(end);
      snapshot_.cutoff_set=true;snapshot_.cutoff=end;
      if(first)capture_tail_deadline_=Clock::now()+std::chrono::seconds(2);
      auto r=accepted();put(r,"stream_id",string(input_handle_));put(r,"end_sample",number(end));return r;
    }
    // A finish that arrives while the session is still opening is the page's
    // real end of speech. The audio admitted so far is held, not yet heard;
    // refusing the finish here lost those words when the session then closed.
    // Take it, and apply it when the open completes.
    if (op == "speech.session.finish_input" && lifecycle_ == "opening" && !session_) {
      require(input_enabled_, "session has no input direction");
      require(str(field(a, "stream_id")) == input_handle_,
              "foreign input handle");
      const auto end = integer(field(a, "end_sample"), aii::voice::input_clock_max);
      require(!early_finish_ || *early_finish_ == static_cast<uint64_t>(end),
              "finish refused: another end is already admitted");
      early_finish_ = static_cast<uint64_t>(end);
      auto r = accepted();
      put(r, "stream_id", string(input_handle_));
      put(r, "end_sample", number(end));
      return r;
    }
    require(session_ && (lifecycle_ == "open" || lifecycle_ == "draining"),
            "session not ready");
    if (op == "speech.session.finish_input") {
      require(input_enabled_,"session has no input direction");
      require(str(field(a, "stream_id")) == input_handle_,
              "foreign input handle");
      const auto end = integer(field(a, "end_sample"), input_limit_ ? input_limit_ : aii::voice::input_clock_max);
      core(aii_voice_finish_input(session_, end, &error_), error_);
#ifdef AII_WITH_ECHO
      if(echo_) echo_cutoff_=end;
#endif
      core(aii_voice_status(session_, &snapshot_, &error_), error_);
      auto r = accepted();
      put(r, "stream_id", string(input_handle_));
      put(r, "end_sample", number(end));
      return r;
    }
    if (op == "speech.session.synthesize") {
      require(lifecycle_ == "open", "synthesis admission closed");
      const auto id = str(field(a, "synthesis_id"));
      const auto text = str(field(a, "text"), 32000);
      require(!synthesis_identities_.count(identity_digest(id)) && generations_.size()<64 &&
                  stream_counter_ < UINT32_MAX,
              "synthesis identity reuse/unresolved capacity/stream exhausted");
      const uint64_t next = current_ + 1;
      core(aii_voice_synthesize(session_, next, text.data(), text.size(),
                                &error_),
           error_);
      auto g = std::make_shared<Generation>();
      g->id = id;
      g->stream = ++stream_counter_;
      generations_[next] = g;
      current_ = next;
      current_name_=id;
      synthesis_identities_.emplace(identity_digest(id),TerminalReceipt{session_epoch_,0,g->stream,false});
      auto r = accepted();
      put(r, "synthesis_id", string(id));
      put(r, "output_stream", number(g->stream));
      return r;
    }
    std::shared_ptr<Generation> g;
    uint64_t id = 0;
    const bool interrupt = op == "speech.session.stop_playback" ||
                           op == "speech.session.cancel_synthesis";
    const auto *requested = field(a, "synthesis_id");
    // The host names the current generation with an empty ID. Preserve the
    // existing omitted/null spelling for interruption only: a playback receipt
    // must identify its generation explicitly, never drift to the newest one.
    const bool current_target = interrupt &&
        (!requested || cJSON_IsNull(requested) ||
         (cJSON_IsString(requested) && requested->valuestring &&
          requested->valuestring[0] == '\0'));
    const auto name=current_target?current_name_:str(requested);
    if (!name.empty()) {
      for (const auto &item : generations_)
        if (item.second->id == name) {
          g = item.second;
          id = item.first;
          break;
        }
      if(!g){
        const auto old=synthesis_identities_.find(identity_digest(name));
        require(old!=synthesis_identities_.end()&&old->second.epoch==session_epoch_&&old->second.settled,"unknown or unresolved synthesis");
        const auto& receipt=old->second;auto r=accepted();
        put(r,"synthesis_id",string(name));put(r,"output_stream",number(receipt.stream));
        if(interrupt){put(r,"output_fenced",boolean(true));put(r,"playback_verified",boolean(false));return r;}
        require(op=="speech.session.playback_report"&&cJSON_GetArraySize(a)==5&&
            integer(field(a,"output_stream"),UINT32_MAX)==receipt.stream&&flag(field(a,"terminal"))&&
            integer(field(a,"rendered_samples"))==receipt.rendered,"settled terminal receipt changed");
        put(r,"rendered_samples",number(receipt.rendered));put(r,"terminal",boolean(true));return r;
      }
    }
    if (interrupt) {
      if (g) {
        g->fenced = true;
        if (op == "speech.session.stop_playback")
          core(aii_voice_stop_playback(session_, id, &error_), error_);
        else
          core(aii_voice_cancel_synthesis(session_, id, &error_), error_);
      }
      auto r = accepted();
      put(r, "synthesis_id", g ? string(g->id) : null());
      put(r, "output_stream", g ? number(g->stream) : null());
      put(r, "output_fenced", boolean(true));
      put(r, "playback_verified", boolean(false));
      return r;
    }
    if (op == "speech.session.playback_report") {
      size_t count = 0;
      for (auto *f = a->child; f; f = f->next)
        ++count;
      require(count == 5 && field(a, "synthesis_id") && g &&
                  integer(field(a, "output_stream"), UINT32_MAX) == g->stream,
              "exact receipt identity required");
      const auto n = integer(field(a, "rendered_samples"));
      const bool terminal = flag(field(a, "terminal"));
      if (!unreconciled_.empty() || unacknowledged(*g, n, terminal)) {
        require(unreconciled_.size() < 64, "playback report backlog full");
        unreconciled_.emplace_back(request, clone(a));
        return Json(nullptr, cJSON_Delete); // answered by reconcile()
      }
      require(n >= g->rendered && n <= g->delivered,
              "impossible render progress");
      require(!g->receipt || (terminal && n == g->rendered),
              "terminal receipt changed");
      require(!terminal || g->fenced || g->ended,
              "natural completion needs transport END");
      core(aii_voice_playback(session_, id, n, terminal, terminal && g->fenced,
                              &error_),
           error_);
      if (!g->receipt && (n != g->rendered || terminal)) {
        advance_drain(); // after native validation, never for an unchanged report
        auto e = object();
        put(e, "synthesis_id", string(g->id));
        put(e, "output_stream", number(g->stream));
        put(e, "sample_rate", number(24000));
        put(e, "rendered_samples", number(n));
        put(e, "delivered_samples", number(g->delivered));
        put(e, "discarded_samples", number(terminal ? g->delivered - n : 0));
        put(e, "terminal", boolean(terminal));
        put(e, "outcome",
            string(terminal ? (g->fenced ? "stopped" : "drained")
                            : "progress"));
        put(e, "evidence", string("host_validated_client_report"));
        put(e, "playback_verified", boolean(false));
        emit("playback_observation", std::move(e));
      }
      g->rendered = n;
      g->receipt = terminal;
      auto r = accepted();
      put(r, "synthesis_id", string(g->id));
      put(r, "output_stream", number(g->stream));
      put(r, "rendered_samples", number(n));
      put(r, "terminal", boolean(terminal));
      return r;
    }
    throw Refused("unknown control operation");
  }
  void pump() {
    if(waveform_publish_.valid()&&waveform_publish_.wait_for(std::chrono::milliseconds(0))==std::future_status::ready) {
      try {
        auto result=waveform_publish_.get();waveform_sha_=str(field(result.get(),"sha256"),64);
        recent_waveforms_.consume_saved(waveform_session_,waveform_end_);
        waveform_state_="saved";
        reply(waveform_request_,waveform_result());waveform_request_=0;
      }catch(const std::exception&) {
        waveform_state_="failed";waveform_reason_="Private waveform publication or readback failed; no saved file is claimed.";
        if(waveform_request_) {refuse(waveform_request_,waveform_reason_.c_str());waveform_request_=0;}
      }
    }
    if(!abort_ && failure_.empty())
      for(auto& observation:attributions_.expire(uint64_t(
          std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now().time_since_epoch()).count())))
        emit("speaker_observation",std::move(observation));
    if(capturing_.valid()&&capturing_.wait_for(std::chrono::milliseconds(0))==std::future_status::ready) {
      try {
        capture_result_=capturing_.get();
        if(!abort_) {
          snapshot_.recognized=input_received_;
          auto e=object();put(e,"stream_id",string(input_handle_));
          put(e,"end_sample",number(input_received_));put(e,"processed_end_sample",number(input_received_));
          input_final_sequence_=emit("input_finished",std::move(e));
        }
      }catch(const std::exception& e) {
        if(!abort_)fail(e.what());
        else {capture_result_=object();put(capture_result_,"state",string("unresolved"));
          put(capture_result_,"reason",string(e.what()));}
      }
    }
    if(enrollment_.valid()&&enrollment_.wait_for(std::chrono::milliseconds(0))==std::future_status::ready) {
      try{
        auto result=enrollment_.get();
        auto* data=cJSON_GetObjectItemCaseSensitive(result.get(),"operation_result");
        auto live=boolean(lifecycle_=="open"&&session_);
        require(cJSON_IsObject(data)&&cJSON_ReplaceItemInObjectCaseSensitive(data,"session_open",live.get()),"speaker readback state unavailable");
        live.release();
        // Management is usable after microphone close; session_open alone does
        // not say whether this is Earbud, finishing input or retained history.
        auto speech=speaker_readback(status().get());
        require(cJSON_AddItemToObject(data,"speech",speech.get()),"speaker readback allocation");
        speech.release();
        reply(enrollment_request_,std::move(result));
      }
      catch(const std::exception& e){refuse(enrollment_request_,e.what());}
    }
    if (opening_.valid() && opening_.wait_for(std::chrono::milliseconds(0)) ==
                                std::future_status::ready) {
      // Settings the engine cannot serve (a speaking language that is not
      // installed, a preset this backend does not hold) are that session's:
      // it fails with the reason and the engine stays ready for the next.
      // Any other failure to open is still the engine's own.
      try {
        session_ = opening_.get();
      } catch (const Refused &e) {
        session_ = nullptr;
        fail_session(e.what());
      }
      if (!session_) {
      } else if (abort_ || !failure_.empty())
        core(aii_voice_close(session_, 1, &error_), error_);
      else {
        lifecycle_ = "open";
        auto e = object(), models = object();
        put(models, "backend", string(backend_name(readiness_)));
        put(models, "operator_settings", clone(effective_.get()));
        if(!cJSON_IsNull(corrections_state_.get()))put(models,"corrections",clone(corrections_state_.get()));
        put(e, "models", std::move(models));
        emit("session_ready", std::move(e));
        if (early_finish_) {
          // The capture limit read with the settings bounds the end, as it
          // bounds a finish that arrives after the open.
          const uint64_t end = input_limit_ ? std::min<uint64_t>(*early_finish_, input_limit_) : *early_finish_;
          early_finish_.reset();
          core(aii_voice_finish_input(session_, end, &error_), error_);
#ifdef AII_WITH_ECHO
          if(echo_) echo_cutoff_=end;
#endif
          core(aii_voice_status(session_, &snapshot_, &error_), error_);
        }
      }
    }
    if (waiting_settings_ && Clock::now() > opening_deadline_)
      fail_session("settings preparation timeout");
    if(capture_) {
      if(!abort_&&snapshot_.cutoff_set&&!end_seen_&&Clock::now()>capture_tail_deadline_)
        fail_session("enrollment capture final tail timeout");
      if(lifecycle_=="draining") {
        if(!capturing_.valid()&&(abort_||input_final_sequence_))terminal();
        else if(Clock::now()>closing_deadline_)fail_session("enrollment capture retirement deadline");
      }
      return;
    }
    if (!session_) {
      if (lifecycle_ == "draining" && !opening_.valid())
        terminal();
      return;
    }
    core(aii_voice_status(session_, &snapshot_, &error_), error_);
    if (*snapshot_.error)
      fail_engine(snapshot_.error);
    {
      std::optional<Ack> ack;
      {
        std::lock_guard<std::mutex> l(mutex_);
        ack.swap(ack_);
      }
      if (ack) {
        if (ack->samples || ack->end)
          advance_drain();
        auto &g = *active_output_->g;
        g.delivered += ack->samples;
        g.seq += ack->frames;
        g.ended |= ack->end;
        pending_audio_ = false;
        active_output_.reset();
        if (!ack->error.empty())
          fail(ack->error);
      }
    }
    aii_voice_event e{};
    char text[131072];
    size_t n = 0;
    for (;;) {
      uint64_t reference=0;
      char track[64]{};
      const auto rc =
          aii_voice_next_event_with_track(session_, &e, &reference, track, sizeof track, text, sizeof text, &n, &error_);
      if (rc == AII_VOICE_AGAIN)
        break;
      if (rc == AII_VOICE_CAPACITY)
        // Named, not collapsed into "native ownership unavailable": the event
        // stays in core custody and the session can never drain past it.
        throw std::runtime_error("native event needs " + std::to_string(n) +
                                 " bytes; the worker's event buffer holds " +
                                 std::to_string(sizeof text));
      core(rc, error_);
      core_taken_ = e.sequence;
      if (abort_ || !failure_.empty())
        continue;
      auto data = object();
      std::string kind = e.kind;
      if(kind=="speaker_observation") {
        const auto key=attributions_.key(reference,sid_,e.start,e.end);
        auto detail=parse(text);
        std::optional<CleanEvidence> clean;
        const auto outcome=str(field(detail.get(),"outcome"),16);
        if(e.clean_track && (outcome=="known" || outcome=="unknown" || field(detail.get(),"speaker_uuid"))) {
          // The core sets this bit only for validated, speaker-specific PCM
          // returned by the separated recognizer. The model result must bind
          // the same segment and immutable policy before its name can escape.
          require(!key.track.empty() && e.evidence_start>=key.start &&
                  e.evidence_start<e.evidence_end && e.evidence_end<=key.end &&
                  integer(field(detail.get(),"samples"),160000)>=31920 &&
                  integer(field(detail.get(),"samples"),160000)<=e.evidence_end-e.evidence_start,
                  "speaker evidence span differs from separated track");
          const auto hash=str(field(detail.get(),"pcm_sha256"),64);
          require(hash.size()==64 && hash.find_first_not_of("0123456789abcdef")==std::string::npos,
                  "speaker evidence recording hash invalid");
          clean=CleanEvidence{key,e.evidence_start,e.evidence_end,
            field(detail.get(),"enrollment_revision")?str(field(detail.get(),"enrollment_revision"),64):"",
            field(detail.get(),"registry_revision")?str(field(detail.get(),"registry_revision"),64):"",
            str(field(detail.get(),"policy_sha256"),64),
            str(field(detail.get(),"embedding_binding"),64)};
        }
        data=attributions_.resolve(reference,key,std::move(detail),clean?&*clean:nullptr);
        if(!cJSON_IsNull(data.get()))emit("speaker_observation",std::move(data));
        continue;
      }
      if (e.generation) {
        auto g = generations_.at(e.generation);
        put(data, "synthesis_id", string(g->id));
        put(data, "output_stream", number(g->stream));
        if (kind == "interruption_requested") {
          g->fenced = true;
          put(data, "reason", string(e.start ? "vad_speech" : text));
        }
      }
      if (kind == "synthesis_end" || kind == "synthesis_cancelled") {
        generations_.at(e.generation)->core_terminal_seen=true;
        continue; // publish only after transport END is written
      }
      if (kind == "input_finished") {
        put(data, "stream_id", string(input_handle_));
        put(data, "end_sample", number(e.start));
        put(data, "processed_end_sample", number(e.end));
        if(*text)put(data,"reason",string(text));
        input_final_sequence_ = sequence_ + 1;
      } else if (!e.generation) {
        put(data, "start_sample", number(e.start));
        put(data, "end_sample", number(e.end));
        if (*text) {
          // Only a transcript's words are corrected; other events carry a
          // reason in this slot. A corrected transcript also carries what
          // was recognized, and both together stay within the one bound a
          // transcript's text has always had.
          const bool transcript=kind=="transcript_partial"||kind=="transcript_final";
          const auto corrected=transcript?corrections_.apply(text):aii::voice::Corrected{};
          if(corrected.applied && corrected.text.size()+std::strlen(text)<sizeof text) {
            put(data, "text", string(corrected.text));
            put(data, "recognized_text", string(text));
            put(data, "corrections", number(corrected.applied));
          } else put(data, "text", string(text));
        }
      }
      if (kind == "pause_query" || kind == "pause_resolved")
        continue;
      if(kind=="transcript_final") {
        // An absent acoustic track is not a segmented-speaker claim. The
        // host deliberately refuses a declared but empty track_id, including
        // when all-speaker listening would otherwise admit the final.
        if (*track) put(data,"track_id",string(track));
        put(data,"attribution",attributions_.add(e.sequence,
            FinalKey{sid_,track,sequence_+1,e.start,e.end},
            uint64_t(std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now().time_since_epoch()).count()),
            readiness_.models_loaded==5));
      }
      const auto public_sequence=emit(kind.c_str(), std::move(data));
      if(kind=="transcript_final"&&readiness_.models_loaded==5) {
        if(enrollment_finals_.size()==16)enrollment_finals_.erase(enrollment_finals_.begin());
        enrollment_finals_[public_sequence]=e.sequence;
      }
    }
#ifdef AII_AUDIO_ACK_TEST_HOOK
    after_event_poll(lifecycle_ == "draining");
#endif
    for (auto &item : generations_) {
      auto &g = *item.second;
      aii_voice_generation state{};
      core(aii_voice_generation_status(session_, item.first, &state, &error_),
           error_);
      if (state.generated > g.observed_generated || (state.retired && !g.observed_retired))
        advance_drain();
      g.observed_generated = state.generated;
      g.observed_retired = state.retired;
      if (state.fenced)
        g.fenced = true;
      if (g.ended && state.retired && !g.terminal_event && !abort_ &&
          failure_.empty()) {
        g.terminal_event = true;
        auto data = object();
        put(data, "synthesis_id", string(g.id));
        put(data, "output_stream", number(g.stream));
        put(data, "delivered_samples", number(g.delivered));
        put(data, "generated_samples", number(state.generated));
        put(data, "playback_verified", boolean(false));
        emit(state.cancelled ? "synthesis_cancelled" : "synthesis_end",
             std::move(data));
      }
    }
    if (!pending_audio_ && !unreconciled_.empty())
      reconcile(); // before settlement, retirement or the next write
    for(auto it=generations_.begin();it!=generations_.end();){
      const auto& g=*it->second;
      if(g.ended&&g.receipt&&g.observed_retired&&g.terminal_event&&g.core_terminal_seen){
        core(aii_voice_release_generation(session_,it->first,&error_),error_);
        auto& receipt=synthesis_identities_.at(identity_digest(g.id));receipt.rendered=g.rendered;receipt.settled=true;
        settled_delivered_+=g.delivered;settled_rendered_+=g.rendered;
        it=generations_.erase(it);
      }else ++it;
    }
    if (!pending_audio_ && !abort_ && failure_.empty()) {
      Output task;
      const auto rc = aii_voice_next_audio(session_, &task.audio, audio_scratch_.data(),
                                           audio_scratch_.size(), &n, &error_);
      if (rc != AII_VOICE_AGAIN) {
        core(rc, error_);
        task.pcm = audio_scratch_.copy(n);
        task.g = generations_.at(task.audio.generation);
        require(task.audio.start == task.g->core_seen,
                "core output clock changed");
        task.g->core_seen += n;
        task.start = task.g->delivered;
        task.seq = task.g->seq;
        pending_audio_ = true;
        active_output_ = task;
        {
          std::lock_guard<std::mutex> l(mutex_);
          output_task_ = std::move(task);
        }
        changed_.notify_all();
      }
    }
    core(aii_voice_status(session_, &snapshot_, &error_), error_);
    if (snapshot_.recognized > drain_recognized_) {
      advance_drain();
      drain_recognized_ = snapshot_.recognized;
    }
    // A verified private WAV publication borrows the same snapshot bridge as
    // enrollment. Its own 30-second deadline bounds it; do not shorten that
    // custody to the ordinary 15-second speech-drain deadline.
    if (lifecycle_ == "draining" && Clock::now() > closing_deadline_ &&
        !waveform_publish_.valid()) {
      if (!abort_)
        fail("native drain made no progress for 15 seconds");
      else if (!snapshot_.retired || pending_audio_)
        abandon(72);
    }
    // THE SESSION ENDS AFTER THE CORE'S LAST EVENT, NEVER AHEAD OF IT. The
    // poll above ran before this status was read, and the core's owners can
    // write the tail's final, input_finished and a speaker observation and
    // then retire in between: ending on "retired" alone released the session
    // with those events untaken, and the host heard a clean session_end
    // without them. The snapshot's sequence is the last event the core
    // numbered, read under the same lock as "retired"; when it is the one
    // last taken, nothing is left and nothing more can come. An aborted or
    // failed session publishes none of them, and ends as before.
    if (snapshot_.retired && !pending_audio_ &&
        (abort_ || !failure_.empty() || snapshot_.sequence == core_taken_))
      terminal();
  }
  void terminal() {
    if (lifecycle_ == "closed" || lifecycle_ == "failed")
      return;
    if(enrollment_.valid()||capturing_.valid()||waveform_publish_.valid())return; // never release borrowed publication custody
    recent_waveforms_.end();
    if(capture_)capture_->abandon(); // no complete/incomplete PCM retained after capture retirement
    for(auto& observation:attributions_.end(!failure_.empty()?"session_failed":
          abort_?"session_aborted":"speaker_result_missing"))
      emit("speaker_observation",std::move(observation));
    if (session_)
      core(aii_voice_release(&session_, &error_), error_);
    uid_snapshot_.cancel();
    if (!failure_.empty()) {
      lifecycle_ = "failed";
      auto e = object();
      put(e, "reason", string(failure_));
      put(e, "scope", string("engine_resources"));
      put(e, "resources_released", boolean(true));
      put(e, "playback_verified", boolean(false));
      put(e, "attributions", attributions_.snapshot());
      emit("failure", std::move(e));
    } else {
      lifecycle_ = "closed";
      auto e = object();
      put(e, "status", string(abort_ ? "aborted" : "completed"));
      put(e, "scope", string("engine_resources"));
      put(e, "playback_verified", boolean(false));
      put(e, "input_samples", number(input_received_));
      put(e, "model_padding_samples",
          number(!capture_&&input_received_ % 512 ? 512 - input_received_ % 512 : 0));
      if(capture_)put(e,"enrollment_capture",clone(capture_result_.get()));
      emit("session_end", std::move(e));
    }
  }
  // A DECLARED GAP IS FILLED WITH THE SILENCE IT REPLACED, AND SAID. The host
  // plane declares a gap when its queue overflowed or the page's capture was
  // lost; recognition of a live conversation cannot know what was spoken in
  // it, but ending the session — and with it the engine process, whose
  // restart takes about a minute and a half — is the wrong answer to a
  // stalled phone. The input clock stays continuous, the endpointer sees
  // silence, and the gap is declared on the worker's own diagnostic line
  // (AII_VOICE_GAP) so it is never hidden. A gap that runs backwards or
  // exceeds kMaxGapSamples is still a fault.
  // queued: the frames waiting behind the pending one, read by the caller
  // under mutex_ (one caller already holds it).
  void report_held(const char *event, std::size_t queued) {
    auto held = object();
    put(held, "component", string("voice-worker"));
    put(held, "event", string(event));
    put(held, "session_id", string(sid_));
    put(held, "received", number(input_received_));
    put(held, "held_ms", number(uint64_t(std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - pending_since_).count())));
    put(held, "queued_frames", number(uint64_t(queued)));
    std::cerr << "AII_VOICE_BACKPRESSURE " << encode(held) << '\n';
  }
  void fill_declared_gap(Frame &f) {
    if (!gap_open_) {
      require(!input_started_ ||
                  (f.stream == input_stream_ && input_seq_ != UINT32_MAX &&
                   f.seq == input_seq_ + 1),
              "audio stream/sequence differs");
      require(f.start >= input_received_, "audio gap runs backwards");
      require(f.start - input_received_ <= kMaxGapSamples, "audio gap too long");
      gap_open_ = true;
      gap_target_ = f.start;
      if (gap_target_ > input_received_) {
        auto gap = object();
        put(gap, "component", string("voice-worker"));
        put(gap, "event", string("input_gap"));
        put(gap, "session_id", string(sid_));
        put(gap, "start", number(input_received_));
        put(gap, "samples", number(gap_target_ - input_received_));
        std::cerr << "AII_VOICE_GAP " << encode(gap) << '\n';
      }
    }
    // A few chunks per pass, so the control wire and the output stay served
    // while a long gap is fed; AGAIN keeps this frame pending and resumes
    // from input_received_.
    for (int chunks = 0; chunks < 8 && input_received_ < gap_target_ && !capture_limit_reached_; ++chunks) {
      uint64_t n = std::min<uint64_t>(gap_target_ - input_received_, kGapChunkSamples);
      if (input_limit_)
        n = std::min<uint64_t>(n, input_limit_ - input_received_);
      if (n == 0)
        break;
      const std::vector<float> silence(n, 0.f);
      const auto rc = aii_voice_feed(session_, input_received_, silence.data(), n, &error_);
      if (rc == AII_VOICE_AGAIN)
        return;
      core(rc, error_);
      recent_waveforms_.feed(input_received_, silence.data(), n);
      input_received_ += n;
      capture_limit_reached_ = input_limit_ && input_received_ == input_limit_;
    }
    if (input_received_ < gap_target_ && !capture_limit_reached_)
      return;
    gap_open_ = false;
    input_started_ = true;
    input_stream_ = f.stream;
    input_seq_ = f.seq;
    input_pending_.reset();
  }
  void input() {
    if (abort_ || !failure_.empty()) {
      if (held_reported_) {
        // The hold ends here: the session's input is discarded, not consumed.
        held_reported_ = false;
        report_held("input_backpressure_cleared", 0);
      }
      input_pending_.reset();
      std::lock_guard<std::mutex> l(mutex_);
      audio_in_.clear();
      audio_samples_ = 0;
      changed_.notify_all();
      return;
    }
    if (!input_pending_) {
      std::lock_guard<std::mutex> l(mutex_);
      if (!audio_in_.empty()) {
        if (held_reported_) {
          held_reported_ = false;
          report_held("input_backpressure_cleared", audio_in_.size());
        }
        pending_since_ = Clock::now();
        audio_samples_ -= audio_in_.front().pcm.size();
        input_pending_ = std::move(audio_in_.front());
        audio_in_.pop_front();
        changed_.notify_all();
      }
    }
#ifdef AII_WITH_ECHO
    // Finish control can precede or follow the final PCM. Drain the held
    // acoustic tail without waiting for another microphone frame. AGAIN
    // retains the exact prepared samples, never reruns adaptation.
    if(echo_ && session_ && echo_cutoff_ && input_received_==*echo_cutoff_) {
      if(!echo_->finished()) {echo_tail_start_=echo_->emitted();echo_tail_=echo_->finish();}
      if(!echo_tail_.empty()) {
        const auto rc=aii_voice_feed(session_,echo_tail_start_,echo_tail_.data(),echo_tail_.size(),&error_);
        if(rc==AII_VOICE_AGAIN)return;
        core(rc,error_);recent_waveforms_.feed(echo_tail_start_,echo_tail_.data(),echo_tail_.size());echo_tail_.clear();
      }
    }
#endif
    if (!input_pending_)
      return;
    if (!held_reported_ && Clock::now() - pending_since_ > kHeldInputReport) {
      held_reported_ = true;
      std::size_t queued = 0;
      {
        std::lock_guard<std::mutex> l(mutex_);
        queued = audio_in_.size();
      }
      report_held("input_backpressure", queued);
    }
    // A frame is another session's when this session declared a stream and
    // the frame is on a different one, or when an earlier session declared
    // the frame's stream: each number serves one session. The second half
    // reaches a session that declares none. A speaker-only session has no
    // input to declare, so an aborted session's unread audio is dropped there
    // too, and is not refused as that session's own contract fault.
    const bool own = declared_stream_ && input_pending_->stream == *declared_stream_;
    if (!own && (declared_stream_ || declared_streams_.count(input_pending_->stream))) {
      if (foreign_frames_++ == 0) {
        auto foreign = object();
        put(foreign, "component", string("voice-worker"));
        put(foreign, "event", string("foreign_input"));
        put(foreign, "session_id", string(sid_));
        put(foreign, "stream", number(uint64_t(input_pending_->stream)));
        put(foreign, "declared", declared_stream_ ? number(uint64_t(*declared_stream_)) : null());
        std::cerr << "AII_VOICE_FOREIGN_INPUT " << encode(foreign) << '\n';
      }
      input_pending_.reset();
      return;
    }
    // An open that declares no stream: a frame that cannot be its first, on a
    // stream an earlier session began, is stale.
    if (!declared_stream_ && !input_started_ && input_pending_->start != 0 &&
        std::find(retired_streams_.begin(), retired_streams_.end(), input_pending_->stream) != retired_streams_.end()) {
      input_pending_.reset();
      return;
    }
    require(input_enabled_,"session has no input direction");
    // The host may already have queued more capture when the engine's finite
    // cutoff arrives. Retire those bytes without admitting them as speech or
    // faulting a completed input. Only the same input stream may be retired.
    if(capture_limit_reached_) {
      const auto& f=*input_pending_;
      require(f.stream==input_stream_,"foreign input after capture limit");
      // A discontinuity after the engine's fixed cutoff describes bytes we
      // already refuse to transcribe; it cannot undo completed input.
      input_pending_.reset();
      return;
    }
    if (lifecycle_ == "closed" && input_final_sequence_) {
      const auto &f = *input_pending_;
      require(!end_seen_ && f.kind == 3 && f.start == input_received_ &&
                  (!input_started_ ||
                   (f.stream == input_stream_ && input_seq_ != UINT32_MAX &&
                    f.seq == input_seq_ + 1)),
              "late audio is not the declared final boundary");
      end_seen_ = true;
      input_pending_.reset();
      return;
    }
    require(lifecycle_ == "opening" || lifecycle_ == "open" ||
                lifecycle_ == "draining",
            "audio before open/after release");
    if (!session_&&!capture_)
      return;
    auto &f = *input_pending_;
    require(!end_seen_, "input ended or discontinuous");
    if (f.kind == 2) {
      // The host declared that samples before f.start were lost or never
      // captured. An enrollment capture must be continuous to be an
      // enrollment, and the paired echo input cannot conceal a lost
      // reference: both stay fatal. A live conversation survives it.
      require(!capture_ && !reference_input_, "input ended or discontinuous");
      fill_declared_gap(f);
      return;
    }
    require(!input_started_ ||
                (f.stream == input_stream_ && input_seq_ != UINT32_MAX &&
                 f.seq == input_seq_ + 1),
            "audio stream/sequence differs");
    require(f.start == input_received_, "audio source clock differs");
    if(capture_) {
      if(f.kind==1) {
        capture_->feed(f.start,f.pcm);input_received_+=f.pcm.size();
      }else {
        capture_->end(f.start);end_seen_=true;
        snapshot_.cutoff_set=true;snapshot_.cutoff=f.start;
        auto pcm=capture_->take();const auto request=capture_->request_id;
        const auto created=capture_->created_ms;const auto policy=uid_policies_->current();
        capture_result_=object();put(capture_result_,"state",string("preparing"));
        capturing_=std::async(std::launch::async,[this,pcm=std::move(pcm),request,created,policy] {
          aii_voice_capture prepared{};aii_voice_error error{};
          require(!capture_cancelled_,"capture preparation cancelled");
          core(aii_voice_prepare_capture(models_,pcm.data(),pcm.size(),&prepared,&error),error);
          require(!capture_cancelled_,"capture cancelled before retention");
          aii::uid::Vector embedding(prepared.embedding,prepared.embedding+aii::uid::embedding_dimensions(prepared.embedding_binding));
          const auto evidence=aii::uid::make_pending_capture(request,created,prepared.samples,prepared.embedding_binding,
              {prepared.pcm_sha256,embedding});
          aii::voice::CaptureEnrollment store(uid_snapshot_,policy);
          auto publication=store.retain(evidence,picosha2::hash256_hex_string(request+"/capture"));
          require(flag(field(publication.get(),"durable"))&&flag(field(publication.get(),"readback_verified")),
              "capture retained bytes are not proven durable; reconcile pending captures before retrying");
          auto result=object();put(result,"state",string("retained"));put(result,"capture_id",string(evidence.id));
          put(result,"samples",number(evidence.samples));put(result,"created_ms",number(evidence.created_ms));
          put(result,"publication",std::move(publication));return result;
        });
      }
      input_started_=true;input_stream_=f.stream;input_seq_=f.seq;input_pending_.reset();return;
    }
    if(reference_input_) {
#ifdef AII_WITH_ECHO
      require(f.pcm.size()%2==0,"incomplete capture/reference pair");
      if(!f.prepared) {
        f.feed_start=echo_->emitted();
        f.input_count=f.pcm.size()/2;
        auto limit=input_limit_;
        if(echo_cutoff_ && (!limit||*echo_cutoff_<limit))limit=*echo_cutoff_;
        if(limit) {require(f.start<=limit,"input past cutoff");f.input_count=std::min<uint64_t>(f.input_count,limit-f.start);}
        if(f.kind==1) {
          require(!f.pcm.empty(),"empty paired PCM");
          f.clean=echo_->feed(f.start,f.pcm.data(),f.input_count);
        }
        if(f.kind==3 || (limit && f.start+f.input_count==limit)) {
          auto tail=echo_->finish();f.clean.insert(f.clean.end(),tail.begin(),tail.end());
        }
        f.prepared=true;
      }
      if(!f.clean.empty()) {
        const auto rc=aii_voice_feed(session_,f.feed_start,f.clean.data(),f.clean.size(),&error_);
        if(rc==AII_VOICE_AGAIN)return;
        core(rc,error_);recent_waveforms_.feed(f.feed_start,f.clean.data(),f.clean.size());
      }
      input_received_+=f.input_count;
      capture_limit_reached_=input_limit_ && input_received_==input_limit_;
      if(f.kind==3){core(aii_voice_finish_input(session_,f.start,&error_),error_);end_seen_=true;}
#endif
    } else if (f.kind == 1) {
      require(!f.pcm.empty(), "empty PCM");
      const auto count=input_limit_ ? std::min<uint64_t>(f.pcm.size(),input_limit_-input_received_) : f.pcm.size();
      const auto rc = aii_voice_feed(session_, f.start, f.pcm.data(),
                                     count, &error_);
      if (rc == AII_VOICE_AGAIN)
        return;
      core(rc, error_);
      recent_waveforms_.feed(f.start,f.pcm.data(),count);
      input_received_ += count;
      capture_limit_reached_=input_limit_ && input_received_==input_limit_;
    } else {
      core(aii_voice_finish_input(session_, f.start, &error_), error_);
      end_seen_ = true;
    }
    input_started_ = true;
    input_stream_ = f.stream;
    input_seq_ = f.seq;
    input_pending_.reset();
  }

public:
  Worker(aii_voice_models *models, aii_voice_readiness ready, int wire,aii::voice::SnapshotBridge& uid,
      const std::string& policy,const std::string& previous)
      : models_(models), uid_snapshot_(uid), readiness_(ready), controls_(0), wire_(wire, true),
        input_(audio_descriptor("AII_AUDIO_IN_FD", true)),
        output_(audio_descriptor("AII_AUDIO_OUT_FD", false), true) {
    uid_snapshot_.sender([this](Json message){send(std::move(message));});
    if(!policy.empty()) {
      uid_policies_.emplace(policy,previous);
      registry_=std::make_unique<aii::voice::SpeakerRegistryStore>(uid_snapshot_,uid_policies_->current(),uid_policies_->previous());
      core(aii_voice_models_track_observer_at(models_,aii::voice::SpeakerRegistryStore::callback_at,registry_.get(),&error_),error_);
    }
  }
  // The bridge outlives this worker; a sender bound to it must not.
  ~Worker() { uid_snapshot_.sender({}); }
  int run() {
    start_threads();
    auto ready = object(), identity = object(), details = object(),
         message = object();
    put(identity, "backend", string(backend_name(readiness_)));
    put(details, "models_loaded", number(readiness_.models_loaded));
    put(details, "accelerator", string(readiness_.accelerator));
    put(details, "accelerator_scope", string("TTS backend summary; per-component configuration is in status.model_execution"));
    put(details, "probe_ms", number(readiness_.probe_ms));
    put(ready, "identity", std::move(identity));
    put(ready, "readiness", std::move(details));
    put(message, "ready", std::move(ready));
    send(std::move(message));
    for (;;) {
      try {
        // A stalled audio sink must not cancel the independent control-wire
        // WriteFile on Windows: it may be halfway through a JSON line carrying
        // the failure report. Only interrupt the pipe whose own deadline ran.
        if (wire_.expired()) {
          wire_.interrupt();
          fault_transport("native control write deadline");
        }
        if (output_.expired()
#ifdef AII_AUDIO_ACK_TEST_HOOK
            && main_loop_sees_audio_deadline()
#endif
        ) {
          output_.interrupt();
          fault_transport("native audio write deadline");
        }
        bool eof, audio_eof;
        std::string error, control;
        {
          std::lock_guard<std::mutex> l(mutex_);
          eof = eof_;
          audio_eof = audio_eof_;
          error = transport_fault_;
          if (!controls_in_.empty()) {
            control = std::move(controls_in_.front());
            controls_in_.pop_front();
            changed_.notify_all();
          }
        }
        // An EOF is ordered after controls the reader already admitted. Their
        // responses must be written (or fault), never discarded as clean EOF.
        if (eof && control.empty() && !quit_) {
          capture_cancelled_=true;
          uid_snapshot_.cancel();
          quit_ = true;
          exit_deadline_ = Clock::now() + std::chrono::seconds(5);
          if (lifecycle_ != "closed" && lifecycle_ != "failed") {
            abort_ = true;
            waiting_settings_ = false;
            lifecycle_ = "draining";
            closing_deadline_ = exit_deadline_;
            for (auto &g : generations_)
              g.second->fenced = true;
            if (session_)
              core(aii_voice_close(session_, 1, &error_), error_);
          }
        }
        if (!error.empty()) {
          if (!quit_) {
            quit_ = true;
            exit_deadline_ = Clock::now() + std::chrono::seconds(5);
          }
          fail_engine(error);
        }
        if (audio_eof && !quit_ && lifecycle_ != "closed" &&
            lifecycle_ != "failed")
          fail("audio endpoint lost; not graceful Finish");
        pump();
        if (!control.empty() && !quit_) {
          auto j = parse(control);
          const auto *settings_reply = field(j.get(), "settings_reply");
          const auto *snapshot_reply = field(j.get(), "snapshot_reply");
          if(snapshot_reply) {
            require(!settings_reply&&!field(j.get(),"id"),"mixed snapshot reply");
            uid_snapshot_.accept(snapshot_reply);
          } else if (settings_reply) {
            try {
              settings(settings_reply);
            } catch (const Refused &e) {
              fail_session(e.what());
            } catch (const std::exception &e) {
              fail_engine(e.what());
            }
          } else {
            const auto id = integer(field(j.get(), "id"));
            require(id > request_id_, "private request ID reused");
            request_id_ = id;
            answer(id, [&] {
              const auto op=str(field(j.get(),"operation"));const auto* a=field(j.get(),"arguments");
              if(waveform_control(id,op,a)||enroll(id,op,a))return;
              if(auto r=admit(id,op,a))reply(id,std::move(r));
            });
          }
        }
        // A CONTRACT FAULT IN ONE SESSION'S INPUT IS THAT SESSION'S. Refused
        // (a frame the session's contract rejects) fails the session and
        // leaves the engine ready for the next, as a refused settings reply
        // already does; a core failure (runtime_error) still ends the
        // process, whose restart is what clears a broken model state.
        if (!quit_) {
          try {
            input();
          } catch (const Refused &e) {
            fail_session(e.what());
          }
        }
        if (quit_ && !session_ && !opening_.valid() && !enrollment_.valid() && !capturing_.valid() && !waveform_publish_.valid() && !pending_audio_)
          break;
        if (quit_ && Clock::now() > exit_deadline_)
          abandon(72);
      } catch (const std::exception &e) {
        if (!quit_) {
          quit_ = true;
          exit_deadline_ = Clock::now() + std::chrono::seconds(5);
        }
        fail_engine(e.what());
      }
      // Never pay a scheduler tick per already-queued frame. On Windows a
      // nominal 1 ms sleep can consume an entire timer quantum, stranding a
      // complete recording behind its unchanged final-tail deadline. One
      // control, pump and input step still run per iteration, so a bulk tail
      // cannot starve Stop/Cancel. A pending frame blocked by the model is not
      // runnable: retain the bounded wait instead of spinning on backpressure.
      std::unique_lock<std::mutex> lock(mutex_);
      const bool idle=!opening_.valid()&&!enrollment_.valid()&&!capturing_.valid()&&!waiting_settings_&&
          !pending_audio_&&generations_.empty()&&!input_pending_&&
          input_received_==snapshot_.recognized&&!snapshot_.recognition_active&&!snapshot_.draining&&
          (!snapshot_.cutoff_set||input_final_sequence_)&&lifecycle_!="draining";
      // Native model threads do not signal changed_. Even an apparently idle
      // open session can acquire an event after our snapshot, so bound that
      // observation delay to 10 ms; reserve the longer wait for no session.
      changed_.wait_for(lock, std::chrono::milliseconds(idle?(session_?10:100):1), [&] {
        return !controls_in_.empty() ||
               (!input_pending_ && !audio_in_.empty()) || ack_.has_value() ||
               (!quit_ && eof_);
      });
    }
    readers_stop_ = true;
    controls_.interrupt();
    input_.interrupt();
    {
      std::lock_guard<std::mutex> l(mutex_);
      settled_ = true;
    }
    changed_.notify_all();
    // Do not lose the deadline watcher while a final response is being written.
    // Windows synchronous I/O needs its owning thread interrupted, not a Close
    // issued by a different thread which may wait behind WriteFile.
    const auto deadline = Clock::now() + std::chrono::seconds(5);
    while (live_threads_) {
      // CancelSynchronousIo only cancels already-pending operations. A reader
      // may have passed its stop check just before the first interrupt above.
      controls_.interrupt();
      input_.interrupt();
      if (wire_.expired()) {
        io_stop_ = true;
        wire_.interrupt();
        fault_transport("native final control write deadline");
      }
      if (output_.expired()) {
        // Keep the healthy control writer alive long enough to deliver its
        // terminal event; a cancelled audio write is its own failure.
        output_.interrupt();
        fault_transport("native final audio write deadline");
      }
      if (Clock::now() > deadline)
        abandon(72);
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    for (auto &t : threads_)
      t.join();
    std::lock_guard<std::mutex> l(mutex_);
    // The process reports its own health: a session failure it contained
    // was already reported by that session and does not fail the exit. An
    // engine failure after it ended the containment (fail_engine).
    return (failure_.empty() || failure_contained_) && transport_fault_.empty() ? 0 : 1;
  }
};
} // namespace
int main(int argc, char **argv) {
  try {
    // Package builders ask the exact worker for its declaration. No model,
    // private state, audio pipe or resident protocol is opened by this path.
    if(argc==2 && std::string(argv[1])=="--describe-settings") {
      std::cout<<encode(OperatorSettings::declarations())<<'\n';
      return 0;
    }
    aii::voice::watch_carrier_liveness();
    const int wire = protocol_stdout();
    std::optional<InstalledProfile> installed;
    std::array<char*,11> pointers{};
    if(argc==1) {
      installed=InstalledProfile::read(std::filesystem::current_path(),InstalledProfile::host_model_root());
      for(size_t i=0;i<pointers.size();++i)pointers[i]=installed->arguments[i].data();
      argv=pointers.data();argc=int(pointers.size());
    }
    require(
        argc == 8 || argc == 9 || argc == 11 || argc == 12,
        "seven verified model paths, optional cpu/vulkan backend, optional UID model and policy required");
#ifndef AII_WITH_UID
    require(argc<11,"worker not linked with native UID");
#endif
    aii::voice::configure_worker_environment(argc>=9 && std::string(argv[8])=="vulkan");
    aii_voice_paths paths{argv[1], argv[2], argv[3], argv[4],
                          argv[5], argv[6], argv[7]};
    aii_voice_models *models = nullptr;
    aii_voice_error error{};
    aii_voice_readiness ready{};
    aii::voice::SnapshotBridge uid;
    std::string policy,previous;
#ifdef AII_WITH_UID
    if(argc>=11) {
      const auto read_policy=[](const std::string& path) {
        std::ifstream f(std::filesystem::u8path(path),std::ios::binary);require(bool(f),"UID policy unavailable");
        char data[4097];f.read(data,sizeof data);const auto n=f.gcount();
        require(f.eof()&&n>0&&n<=4096,"UID policy byte/read bound");return std::string(data,size_t(n));
      };
      policy=read_policy(argv[10]);
      const auto previous_path=installed?installed->previous_uid_policy:argc==12?std::string(argv[11]):std::string{};
      if(!previous_path.empty())previous=read_policy(previous_path);
      core(aii_voice_models_load_uid_policies(&paths,argv[8],argv[9],policy.data(),policy.size(),
        aii::voice::SnapshotBridge::callback,&uid,
        installed&&!installed->asr_execution.empty()?installed->asr_execution.c_str():nullptr,
        previous.empty()?nullptr:previous.data(),previous.size(),&models,&error),error);
    } else
#endif
    if(argc==9)core(aii_voice_models_load_with_backend(&paths,argv[8],&models,&error),error);
    else core(aii_voice_models_load(&paths, &models, &error), error);
    core(aii_voice_models_warm(models, &ready, &error), error);
    int result;
    {
      Worker worker(models, ready, wire,uid,policy,previous);
      result = worker.run();
    }
#ifdef _WIN32
    // The worker's threads are joined and its protocol output is closed: it
    // has said all it will, so it ends now (worker_io.h end_process). Freeing
    // the models and returning from main first took 3 to 4 seconds of the
    // carrier's five-second retirement bound on the Windows Full qualification
    // VM: the model release, then DLL process-detach work, most of it
    // torch_cpu deregistering its operators. The kernel reclaims the models'
    // memory, handles and GPU allocations of the ended process either way.
    aii::voice::wire::end_process(result);
#else
    core(aii_voice_models_release(&models, &error), error);
    return result;
#endif
  } catch (const std::exception &e) {
    std::cerr << "native voice worker: " << e.what() << '\n';
#ifdef _WIN32
    aii::voice::wire::end_process(1);
#else
    return 1;
#endif
  }
}
