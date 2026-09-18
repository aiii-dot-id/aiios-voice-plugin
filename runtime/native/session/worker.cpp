// Private Go-carrier worker. Public JSON-RPC remains solely in the Go SDK.
// This process has no Python, microphone/speaker, network or enrollment store.
#include "c_api.h"
#include "worker_io.h"
#include "worker_audio_scratch.h"
#include "worker_json.h"
#include "speaker_observation.h"
#include "snapshot_bridge.h"
#include "capture_enrollment.h"
#include "capture_input.h"
#include "../../native_uid/enrollment.h"
#include "../../native_uid/bound_policies.h"
#include "../vendor/picosha2/picosha2.h"
#include "operator_settings.h"
#include "installed_profile.h"
#include "confirmed_acts.h"
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
using namespace aii::voice::wire;
using Clock = std::chrono::steady_clock;
#ifndef AII_WORKER_BACKEND
#define AII_WORKER_BACKEND "native-common-cpu"
#endif
namespace {
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
};
struct Generation {
  std::string id;
  uint32_t stream = 0, seq = 0;
  uint64_t core_seen = 0, delivered = 0, rendered = 0;
  bool ended = false, receipt = false, terminal_event = false;
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
class Worker {
  aii_voice_models *models_;
  aii::voice::SnapshotBridge& uid_snapshot_;
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
  aii_voice_session *session_ = nullptr;
  std::future<aii_voice_session *> opening_;
  std::future<Json> enrollment_;
  std::optional<aii::voice::CaptureInput> capture_;
  std::future<Json> capturing_;
  std::atomic<bool> capture_cancelled_{false};
  Json capture_result_=null();
  Clock::time_point capture_tail_deadline_;
  uint64_t enrollment_request_=0;
  std::optional<aii::uid::BoundPolicies> uid_policies_;
  aii::voice::ConfirmedActs enrollment_acts_;
  std::string sid_, input_handle_, lifecycle_ = "closed", failure_;
  std::set<std::string> used_sessions_, used_synthesis_;
  std::map<uint64_t, std::string> issued_settings_;
  std::map<uint64_t,uint64_t> transcript_sequences_;
  std::map<uint64_t,uint64_t> enrollment_finals_; // public final -> native final
  std::map<uint64_t, std::shared_ptr<Generation>> generations_;
  uint64_t sequence_ = 0, request_id_ = 0, settings_id_ = 0,
           input_received_ = 0, current_ = 0, input_final_sequence_ = 0;
  uint32_t stream_counter_ = 0, input_stream_ = 0, input_seq_ = 0;
  bool input_started_ = false, end_seen_ = false;
  uint64_t input_limit_ = 0;
  bool capture_limit_reached_ = false;
  bool waiting_settings_ = false, pending_audio_ = false, abort_ = false,
       quit_ = false;
  Clock::time_point opening_deadline_, closing_deadline_, exit_deadline_;
  std::optional<Frame> input_pending_;
  std::optional<Output> active_output_;
  AudioScratch<> audio_scratch_;
  Json processing_ = null(), effective_ = object();
  aii_voice_snapshot snapshot_{};
  aii_voice_error error_{};

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
        } catch (const std::exception &e) {
          ack.error = e.what();
        }
        {
          std::lock_guard<std::mutex> l(mutex_);
          ack_ = std::move(ack);
        }
        changed_.notify_all();
      }
    });
  }
  void fail(const std::string &reason) {
    if (!failure_.empty())
      return;
    failure_ = reason;
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
  Json status() {
    auto r = object();
    char execution[16385]{};size_t required=0;
    core(aii_voice_models_execution(models_,execution,sizeof execution,&required,&error_),error_);
    put(r,"model_execution",parse(execution));
    put(r, "session_id", string(sid_));
    put(r, "state_sequence", number(sequence_));
    put(r, "lifecycle", string(lifecycle_));
    put(r, "reason", failure_.empty() ? null() : string(failure_));
    put(r, "operator_settings", clone(effective_.get()));
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
      put(p, "engine_echo_cancellation", boolean(false));
      put(input, "processing", std::move(p));
    }
    put(input, "state",
        string(input_final_sequence_  ? "finished"
               : snapshot_.cutoff_set ? "finishing"
                                      : "accepting"));
    put(input, "admitted_end_sample",
        snapshot_.cutoff_set ? number(snapshot_.cutoff) : null());
    put(input, "received_end_sample", number(input_received_));
    put(input, "processed_end_sample", number(snapshot_.recognized));
    put(r, "input", std::move(input));
    auto rec = object();
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
        string(current_ ? generations_.at(current_)->id : ""));
    put(synth, "state",
        string(snapshot_.synthesizing ? "running"
               : current_             ? "finished"
                                      : "idle"));
    put(r, "synthesis", std::move(synth));
    uint64_t delivered = 0, rendered = 0, queued = 0, discarded = 0;
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
        string(current_ ? generations_.at(current_)->id : ""));
    put(playback, "state", string(unresolved ? "unobserved" : "idle"));
    put(playback, "queued_samples", number(queued));
    put(playback, "delivered_samples", number(delivered));
    put(playback, "rendered_samples", number(rendered));
    put(playback, "discarded_samples", number(discarded));
    put(playback, "sample_rate", number(24000));
    put(playback, "evidence",
        string("client_reports_not_acoustic_measurements"));
    put(r, "playback", std::move(playback));
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
  Json open(const cJSON *a) {
    require(lifecycle_ == "closed" && !session_ && !opening_.valid()&&!enrollment_.valid()&&!capturing_.valid(),
            "prior resources not released");
    std::optional<aii::voice::CaptureInput> capture;
    if(const auto* requested=field(a,"enrollment_capture")) {
      require(uid_policies_&&uid_policies_->current().policy.minimum_enrollment_samples==1&&readiness_.models_loaded==5,
          "guided capture needs an explicitly bound single-recording policy");
      capture.emplace(requested);
    }
    const auto id = str(field(a, "session_id"), 128);
    require(!used_sessions_.count(id) && used_sessions_.size() < 1024,
            "session ID reuse/limit");
    const auto handle = str(field(a, "input_handle"));
    str(field(a, "output_handle"));
    const auto *audio = field(a, "audio");
    require(cJSON_IsObject(audio), "audio object required");
    require(str(field(audio, "format")) == "s16le", "explicit s16le required");
    for (const char *name : {"input", "output"}) {
      const auto *f = field(audio, name);
      require(cJSON_IsObject(f) && integer(field(f, "rate"), 192000) >= 8000,
              "audio rate invalid");
      const auto channels = integer(field(f, "channels"), 2);
      require(channels >= 1, "audio channels invalid");
    }
    validate_processing(field(field(audio, "input"), "processing"));
    sid_ = id;
    uid_snapshot_.begin(sid_);
    transcript_sequences_.clear();
    enrollment_finals_.clear();
    input_handle_ = handle;
    used_sessions_.insert(id);
    sequence_ = 0;
    failure_.clear();
    abort_ = false;
    current_ = 0;
    generations_.clear();
    input_started_ = false;
    end_seen_ = false;
    input_limit_ = 0;
    capture_limit_reached_ = false;
    input_received_ = input_final_sequence_ = 0;
    snapshot_ = {};
    capture_=std::move(capture);capture_result_=null();capture_cancelled_=false;
    effective_ = object();
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
    put(in, "channels", number(1));
    put(out, "rate", number(24000));
    put(out, "channels", number(1));
    put(formats, "input", std::move(in));
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
    waiting_settings_ = false;
    opening_ = std::async(std::launch::async, [&, config] {
      aii_voice_error e{};
      aii_voice_session *s = nullptr;
      const auto speech=config.speech();
      core(aii_voice_open_with_capture_limit(models_, &config.control, &speech, config.capture_limit_minutes, &s, &e), e);
      return s;
    });
  }
  bool enroll(uint64_t request,const std::string& op,const cJSON* a) {
    if(op!="speaker.enroll"&&op!="speaker.list"&&op!="speaker.remove"&&op!="speaker.reset"&&op!="speaker.discard_capture"&&op!="speaker.upgrade_policy")return false;
    require(cJSON_IsObject(a)&&uid_policies_.has_value()&&readiness_.models_loaded==5,"native UID operation unavailable");
    require(op!="speaker.upgrade_policy"||lifecycle_=="closed","close speech before confirmed enrollment policy upgrade");
    const bool guided=field(a,"capture_id")!=nullptr;
    const bool captures=op=="speaker.enroll"&&!guided;
    const auto capture_id=guided?str(field(a,"capture_id"),64):"";
    require(!guided||(capture_id.size()==64&&capture_id.find_first_not_of("0123456789abcdef")==std::string::npos&&
        !field(a,"finals")&&(op=="speaker.enroll"||op=="speaker.discard_capture")),"exact capture_id without live finals required");
    require(op!="speaker.discard_capture"||guided,"capture_id required");
    require(!field(a,"session_id")||str(field(a,"session_id"),128)==sid_,"session_id differs; call speaker.list without a session_id to inspect current state");
    require(!enrollment_.valid()&&!capturing_.valid()&&(!capture_||lifecycle_=="closed")&&
        (lifecycle_=="open"||lifecycle_=="closed"),"speaker management waits for capture close, opening, closing or enrollment");
    require(!captures||(field(a,"session_id")&&lifecycle_=="open"&&session_),"enrollment requires live finalized recordings; call speaker.list for session_open and eligible_final_sequences");
    const bool mutates=op!="speaker.list";std::string act;
    if(mutates) {
      const auto* stamp=field(a,"_host_operator_act");
      require(cJSON_IsObject(stamp),"operator confirmation required");
      act=str(field(stamp,"id"),128);str(field(stamp,"confirmed_at"),64);
      // A repeated one-use confirmation refuses this operation, not the
      // resident speech session. Allocation/runtime failures still fault it.
      try { enrollment_acts_.check(act); }
      catch (const std::invalid_argument& e) { throw Refused(e.what()); }
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
    if(lifecycle_=="closed")uid_snapshot_.begin("uid-management-"+std::to_string(request));
    if(mutates)enrollment_acts_.consume(act);
    // "auto" may legitimately recur under the host's standing confirmation;
    // each admitted request still gets its own staging identity.
    const auto upload=picosha2::hash256_hex_string(sid_+std::string(1,'\0')+act+std::string(1,'\0')+std::to_string(request));
    const auto policies=*uid_policies_;const auto policy=policies.current();auto* session=session_;enrollment_request_=request;
    const auto session_id=sid_;const auto final_map=enrollment_finals_;
    enrollment_=std::async(std::launch::async,[this,op,mutates,guided,capture_id,speaker,label,selected,upload,policies,policy,session,session_id,final_map] {
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
        if(!detail.empty())put(result,"detail",string(detail));
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
      if(op=="speaker.upgrade_policy")put(data,"reconciled",boolean(!upgrade_required));
      if(!mutates) {
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
      if(!durable)put(result,"detail",string("Enrollment bytes read back, but publication durability is unknown; do not assume unchanged or repeat automatically."));
      put(result,"operation_result",std::move(data));return result;
    });
    return true;
  }
  Json admit(const std::string &op, const cJSON *a) {
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
      require(mode == "abort" || ((session_||capture_) && snapshot_.cutoff_set),
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
    require(session_ && (lifecycle_ == "open" || lifecycle_ == "draining"),
            "session not ready");
    if (op == "speech.session.finish_input") {
      require(str(field(a, "stream_id")) == input_handle_,
              "foreign input handle");
      const auto end = integer(field(a, "end_sample"), input_limit_ ? input_limit_ : aii::voice::input_clock_max);
      core(aii_voice_finish_input(session_, end, &error_), error_);
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
      require(!used_synthesis_.count(id) && used_synthesis_.size() < 4096 &&
                  stream_counter_ < UINT32_MAX,
              "synthesis identity reuse/limit");
      const uint64_t next = current_ + 1;
      core(aii_voice_synthesize(session_, next, text.data(), text.size(),
                                &error_),
           error_);
      auto g = std::make_shared<Generation>();
      g->id = id;
      g->stream = ++stream_counter_;
      generations_[next] = g;
      current_ = next;
      used_synthesis_.insert(id);
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
    if (!current_target) {
      const auto name = str(requested);
      for (const auto &item : generations_)
        if (item.second->id == name) {
          g = item.second;
          id = item.first;
          break;
        }
      require(bool(g), "unknown synthesis");
    } else if (current_) {
      g = generations_.at(current_);
      id = current_;
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
        reply(enrollment_request_,std::move(result));
      }
      catch(const std::exception& e){refuse(enrollment_request_,e.what());}
    }
    if (opening_.valid() && opening_.wait_for(std::chrono::milliseconds(0)) ==
                                std::future_status::ready) {
      session_ = opening_.get();
      if (abort_ || !failure_.empty())
        core(aii_voice_close(session_, 1, &error_), error_);
      else {
        lifecycle_ = "open";
        auto e = object(), models = object();
        put(models, "backend", string(backend_name(readiness_)));
        put(models, "operator_settings", clone(effective_.get()));
        put(e, "models", std::move(models));
        emit("session_ready", std::move(e));
      }
    }
    if (waiting_settings_ && Clock::now() > opening_deadline_)
      fail("settings preparation timeout");
    if(capture_) {
      if(!abort_&&snapshot_.cutoff_set&&!end_seen_&&Clock::now()>capture_tail_deadline_)
        fail("enrollment capture final tail timeout");
      if(lifecycle_=="draining") {
        if(!capturing_.valid()&&(abort_||input_final_sequence_))terminal();
        else if(Clock::now()>closing_deadline_)fail("enrollment capture retirement deadline");
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
      fail(snapshot_.error);
    {
      std::optional<Ack> ack;
      {
        std::lock_guard<std::mutex> l(mutex_);
        ack.swap(ack_);
      }
      if (ack) {
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
      const auto rc =
          aii_voice_next_event_with_reference(session_, &e, &reference, text, sizeof text, &n, &error_);
      if (rc == AII_VOICE_AGAIN)
        break;
      if (rc == AII_VOICE_CAPACITY)
        // Named, not collapsed into "native ownership unavailable": the event
        // stays in core custody and the session can never drain past it.
        throw std::runtime_error("native event needs " + std::to_string(n) +
                                 " bytes; the worker's event buffer holds " +
                                 std::to_string(sizeof text));
      core(rc, error_);
      if (abort_ || !failure_.empty())
        continue;
      auto data = object();
      std::string kind = e.kind;
      if(kind=="speaker_observation") {
        require(reference&&transcript_sequences_.count(reference),"UID observation lacks its public final");
        data=speaker_observation(parse(text),transcript_sequences_.at(reference));
        emit("speaker_observation",std::move(data));
        transcript_sequences_.erase(reference);
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
      if (kind == "synthesis_end" || kind == "synthesis_cancelled")
        continue; // publish only after transport END is written
      if (kind == "input_finished") {
        put(data, "stream_id", string(input_handle_));
        put(data, "end_sample", number(e.start));
        put(data, "processed_end_sample", number(e.end));
        if(*text)put(data,"reason",string(text));
        input_final_sequence_ = sequence_ + 1;
      } else if (!e.generation) {
        put(data, "start_sample", number(e.start));
        put(data, "end_sample", number(e.end));
        if (*text)
          put(data, "text", string(text));
      }
      if (kind == "pause_query" || kind == "pause_resolved")
        continue;
      const auto public_sequence=emit(kind.c_str(), std::move(data));
      if(kind=="transcript_final"&&readiness_.models_loaded==5) {
        require(transcript_sequences_.size()<8,"unresolved UID observations exceeded bound");
        transcript_sequences_[e.sequence]=public_sequence;
        if(enrollment_finals_.size()==16)enrollment_finals_.erase(enrollment_finals_.begin());
        enrollment_finals_[public_sequence]=e.sequence;
      }
    }
    for (auto &item : generations_) {
      auto &g = *item.second;
      aii_voice_generation state{};
      core(aii_voice_generation_status(session_, item.first, &state, &error_),
           error_);
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
    if (lifecycle_ == "draining" && Clock::now() > closing_deadline_) {
      if (!abort_)
        fail("native drain exceeded 15 seconds");
      else if (!snapshot_.retired || pending_audio_)
        std::_Exit(72);
    }
    if (snapshot_.retired && !pending_audio_)
      terminal();
  }
  void terminal() {
    if (lifecycle_ == "closed" || lifecycle_ == "failed")
      return;
    if(enrollment_.valid()||capturing_.valid())return; // never release borrowed preparation/publication custody
    if(capture_)capture_->abandon(); // no complete/incomplete PCM retained after capture retirement
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
  void input() {
    if (abort_ || !failure_.empty()) {
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
        audio_samples_ -= audio_in_.front().pcm.size();
        input_pending_ = std::move(audio_in_.front());
        audio_in_.pop_front();
        changed_.notify_all();
      }
    }
    if (!input_pending_)
      return;
    // The host may already have queued more capture when the engine's finite
    // cutoff arrives. Retire those bytes without admitting them as speech or
    // faulting a completed input. Only the same input stream may be retired.
    if(capture_limit_reached_) {
      const auto& f=*input_pending_;
      require(f.stream==input_stream_ && f.kind!=2,"foreign/discontinuous input after capture limit");
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
    require(!end_seen_ && f.kind != 2, "input ended or discontinuous");
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
          aii::uid::Vector embedding{};std::copy(std::begin(prepared.embedding),std::end(prepared.embedding),embedding.begin());
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
    if (f.kind == 1) {
      require(!f.pcm.empty(), "empty PCM");
      const auto count=input_limit_ ? std::min<uint64_t>(f.pcm.size(),input_limit_-input_received_) : f.pcm.size();
      const auto rc = aii_voice_feed(session_, f.start, f.pcm.data(),
                                     count, &error_);
      if (rc == AII_VOICE_AGAIN)
        return;
      core(rc, error_);
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
    if(!policy.empty())uid_policies_.emplace(policy,previous);
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
        if (wire_.expired() || output_.expired()) {
          wire_.interrupt();
          output_.interrupt();
          fault_transport("native output write deadline");
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
          fail(error);
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
            } catch (const std::exception &e) {
              fail(e.what());
            }
          } else {
            const auto id = integer(field(j.get(), "id"));
            require(id > request_id_, "private request ID reused");
            request_id_ = id;
            try {
              const auto op=str(field(j.get(),"operation"));const auto* a=field(j.get(),"arguments");
              if(!enroll(id,op,a))reply(id,admit(op,a));
            } catch (const Refused &e) {
              refuse(id, e.what());
            } catch (const std::exception &e) {
              fail(e.what());
              refuse(id, e.what());
            }
          }
        }
        if (!quit_)
          input();
        if (quit_ && !session_ && !opening_.valid() && !enrollment_.valid() && !capturing_.valid() && !pending_audio_)
          break;
        if (quit_ && Clock::now() > exit_deadline_)
          std::_Exit(72);
      } catch (const std::exception &e) {
        if (!quit_) {
          quit_ = true;
          exit_deadline_ = Clock::now() + std::chrono::seconds(5);
        }
        fail(e.what());
      }
      // Never pay a scheduler tick per already-queued frame. On Windows a
      // nominal 1 ms sleep can consume an entire timer quantum, stranding a
      // complete recording behind its unchanged final-tail deadline. One
      // control, pump and input step still run per iteration, so a bulk tail
      // cannot starve Stop/Cancel. A pending frame blocked by the model is not
      // runnable: retain the bounded wait instead of spinning on backpressure.
      std::unique_lock<std::mutex> lock(mutex_);
      changed_.wait_for(lock, std::chrono::milliseconds(1), [&] {
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
      if (wire_.expired() || output_.expired()) {
        io_stop_ = true;
        wire_.interrupt();
        output_.interrupt();
        fault_transport("native final output write deadline");
      }
      if (Clock::now() > deadline)
        std::_Exit(72);
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    for (auto &t : threads_)
      t.join();
    std::lock_guard<std::mutex> l(mutex_);
    return failure_.empty() && transport_fault_.empty() ? 0 : 1;
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
    const int wire = protocol_stdout();
    std::optional<InstalledProfile> installed;
    std::array<char*,11> pointers{};
    if(argc==1) {
#ifdef _WIN32
      char* snapshot=nullptr;size_t length=0;
      const int env_error=_dupenv_s(&snapshot,&length,"AII_MODELS_DIR");
      std::unique_ptr<char,decltype(&std::free)> owned(snapshot,std::free);
      require(!env_error,"cannot read host-provided model root");
      const char* models=owned.get();
#else
      const char* models=std::getenv("AII_MODELS_DIR");
#endif
      require(models&&*models,"host-provided model root required");
      installed=InstalledProfile::read(std::filesystem::current_path(),std::filesystem::u8path(models));
      for(size_t i=0;i<pointers.size();++i)pointers[i]=installed->arguments[i].data();
      argv=pointers.data();argc=int(pointers.size());
    }
    require(
        argc == 8 || argc == 9 || argc == 11 || argc == 12,
        "seven verified model paths, optional cpu/vulkan backend, optional UID model and policy required");
#ifndef AII_WITH_UID
    require(argc<11,"worker not linked with native UID");
#endif
    // The executable owns its offline policy, before any inference library is
    // initialized. The embedding C API still requires its caller to set it.
#ifdef _WIN32
    if (_putenv_s("ORT_DISABLE_TELEMETRY", "1"))
      throw std::runtime_error("cannot disable native telemetry");
    if(argc>=9 && std::string(argv[8])=="vulkan" &&
       (_putenv_s("GGML_VK_DISABLE_F16","1") || _putenv_s("GGML_VK_VISIBLE_DEVICES","0")))
      throw std::runtime_error("cannot bind explicit Vulkan precision/device");
#else
    if (setenv("ORT_DISABLE_TELEMETRY", "1", 1))
      throw std::runtime_error("cannot disable native telemetry");
    if(argc>=9 && std::string(argv[8])=="vulkan" &&
       (setenv("GGML_VK_DISABLE_F16","1",1) ||
#if defined(__linux__) && !defined(__ANDROID__)
        // Runtime owns placement. Expose the hardware registry to its selector
        // instead of pinning a machine-dependent physical ordinal to zero.
        unsetenv("GGML_VK_VISIBLE_DEVICES")
#else
        setenv("GGML_VK_VISIBLE_DEVICES","0",1)
#endif
       ))
      throw std::runtime_error("cannot bind explicit Vulkan precision/device");
#endif
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
        std::ifstream f(path,std::ios::binary);require(bool(f),"UID policy unavailable");
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
    core(aii_voice_models_release(&models, &error), error);
    return result;
  } catch (const std::exception &e) {
    std::cerr << "native voice worker: " << e.what() << '\n';
    return 1;
  }
}
