#include "c_api_internal.h"
#include "worker_io.h"
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <cstdio>
#ifndef _WIN32
#include <unistd.h>
#else
#include <windows.h>
#endif
namespace {
std::string fixture_setting(const char *name) {
#ifdef _WIN32
  char *value = nullptr;
  size_t length = 0;
  if (_dupenv_s(&value, &length, name) || !value)
    return {};
  std::string result(value);
  std::free(value);
  return result;
#else
  const char *value = std::getenv(name);
  return value ? value : "";
#endif
}
}
namespace aii::voice::wire {
void before_audio_ack(bool end, bool failed) {
  static std::atomic<bool> held{false};
  const auto kind = fixture_setting("AII_FIXTURE_HOLD_AUDIO_ACK");
  if (kind != (failed ? "failed" : end ? "end" : "pcm") || held.exchange(true))
    return;
  const std::filesystem::path gate = fixture_setting("AII_FIXTURE_AUDIO_ACK_GATE");
  std::ofstream(gate / "held").put('1');
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
  while (!std::filesystem::exists(gate / "release") && std::chrono::steady_clock::now() < deadline)
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
}
bool main_loop_sees_audio_deadline() {
  static const bool main_loop = fixture_setting("AII_FIXTURE_AUDIO_DEADLINE") != "writer";
  return main_loop;
}
void after_event_poll(bool draining) {
  static const auto hold = fixture_setting("AII_FIXTURE_HOLD_AFTER_EVENT_POLL_MS");
  if (draining && !hold.empty())
    std::this_thread::sleep_for(std::chrono::milliseconds(std::stoi(hold)));
}
namespace {
std::atomic<bool> breaking{false}, reset_waiting{false}, loop_waiting{false};
const std::string &reset_fails_at() {
  static const std::string place = fixture_setting("AII_FIXTURE_RESET_FAILS_AT");
  return place;
}
std::chrono::steady_clock::time_point ten_seconds() {
  return std::chrono::steady_clock::now() + std::chrono::seconds(10);
}
bool past(std::chrono::steady_clock::time_point deadline) {
  if (std::chrono::steady_clock::now() >= deadline)
    return true;
  std::this_thread::sleep_for(std::chrono::milliseconds(1));
  return false;
}
void break_began() { breaking = !reset_fails_at().empty(); }
void reset_waits_for_its_place() {
  if (!breaking)
    return;
  const auto deadline = ten_seconds();
  reset_waiting = true;
  while (!loop_waiting && !past(deadline)) {
  }
  reset_waiting = false;
  breaking = false;
}
void fail_reset_here(aii_voice_session *session, const char *place, bool retired_too) {
  const auto deadline = ten_seconds();
  aii_voice_snapshot core{};
  aii_voice_error error{};
  const auto failed = [&] { return *core.error && (core.retired || !retired_too); };
  loop_waiting = true;
  while (aii_voice_status(session, &core, &error) == AII_VOICE_OK && !failed() && !past(deadline)) {
  }
  loop_waiting = false;
  if (failed())
    log_line(std::string("AII_FIXTURE_ORDER the reset failed at ") + place);
}
}
void before_last_status(aii_voice_session *session) {
  if (reset_waiting && reset_fails_at() == "last_status")
    fail_reset_here(session, "last_status", true);
}
void before_input_frame(aii_voice_session *session) {
  if (!session || !breaking || reset_fails_at() != "input")
    return;
  const auto deadline = ten_seconds();
  while (!reset_waiting && !past(deadline)) {
  }
  if (reset_waiting)
    fail_reset_here(session, "input", false);
}
}
namespace {
struct Asr : aii::voice::Recognizer {
  bool separated_track=false;
  std::vector<float> selected;
  std::vector<std::string> terms;
  size_t prefer(const std::vector<std::string>& list) override {
    terms.clear();
    for(const auto& term:list)if(!term.empty()&&term.find_first_of(" ,.")==std::string::npos)terms.push_back(term);
    return terms.size();
  }
  std::string written(std::string text) const {
    for(const auto& term:terms) {
      std::string lower=term;for(auto& c:lower)if(c>='A'&&c<='Z')c=char(c-'A'+'a');
      for(size_t at=0;(at=text.find(lower,at))!=std::string::npos;at+=term.size())
        if((!at||text[at-1]==' ')&&(at+lower.size()==text.size()||text[at+lower.size()]==' '))text.replace(at,lower.size(),term);
    }
    return text;
  }
  void begin() override {selected.clear();}
  std::string push(const float *pcm, size_t count) override {
    if(separated_track){selected.insert(selected.end(),pcm,pcm+count);return {};}
    return written("opening words");
  }
  std::string finish() override { return separated_track?"":written("opening words retained"); }
  bool separated() const override {return separated_track;}
  std::vector<aii::voice::RecognizedSegment> segments() const override {
    if(!separated_track)return {};
    return {{"track-a","opening words retained",0,selected.size(),0,selected}};
  }
  void reset() override {selected.clear();}
  void cancel() noexcept override {}
};
struct Vad : aii::voice::Vad {
  const bool level = fixture_setting("AII_FIXTURE_VAD") == "level";
  void reset() override {}
  float score(const float *p) override { return !level || std::abs(p[0]) > .1f ? .9f : 0.f; }
};
struct Endpoint : aii::voice::Endpoint {
  std::atomic<bool> cancelled{false};
  void open() override { cancelled = false; }
  double score(uint64_t, const std::vector<float> &) override {
    const auto hold = fixture_setting("AII_FIXTURE_ENDPOINT_MS");
    if (!hold.empty()) {
      const auto until = std::chrono::steady_clock::now() + std::chrono::milliseconds(std::stoi(hold));
      while (!cancelled && std::chrono::steady_clock::now() < until)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return .9;
  }
  void cancel() noexcept override { cancelled = true; }
};
struct Tts : aii::voice::Synthesizer {
  bool second=false;
  static bool held(const aii::voice::SpeechSettings& s,bool checking) {
    auto base=s;base.voice="alba";
    return base.defaults() && (s.voice=="alba" || s.voice=="javert" || (checking && s.voice=="fantine"));
  }
  void check(const aii::voice::SpeechSettings& s) const override {
    if(!held(s,true))throw std::invalid_argument("speech settings unsupported by this backend");
  }
  void configure(const aii::voice::SpeechSettings& s) override {
    if(!held(s,false))throw std::invalid_argument("speech settings unsupported by this backend");
    second=s.voice=="javert";
  }
  void open() override {
    const std::string gate=fixture_setting("AII_FIXTURE_OPEN_GATE");
    if(gate.empty())return;
    const std::filesystem::path dir(gate);
    std::ofstream(dir/"held").put('1');
    while(!std::filesystem::exists(dir/"release"))std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  std::atomic<bool>* release_uid=nullptr;
  std::atomic<bool> cancelled{false};
  std::string text;
  unsigned count = 0;
  void start(uint64_t, const std::string &t) override {
    if(release_uid && t=="Release UID.")*release_uid=true;
    text = t;
    count = 0;
    cancelled = false;
    if (t == "Break.")
      aii::voice::wire::break_began();
  }
  std::vector<float> next() override {
    if (text == "Stall.")
      for (;;) std::this_thread::sleep_for(std::chrono::milliseconds(10));
    if (text == "Break." && !count++)
      return std::vector<float>(960, .25f);
    if (text == "Hold." || text == "Break.")
      while (!cancelled)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    if (cancelled)
      throw aii::voice::Cancelled("fixture cancellation");
    if (text == "Flood.")
      return ++count <= 20 ? std::vector<float>(32768, .25f)
                           : std::vector<float>{};
    if (text == "Slow." && !count)
      std::this_thread::sleep_for(std::chrono::milliseconds(std::stoi(fixture_setting("AII_FIXTURE_SLOW_CALL_MS"))));
    return count++ ? std::vector<float>{} : std::vector<float>(second ? 480 : 960, .25f);
  }
  void reset() override {
    if (text != "Break.")
      return;
    text.clear();
    aii::voice::wire::reset_waits_for_its_place();
    throw std::runtime_error("fixture synthesizer reset failed");
  }
  void cancel(uint64_t) noexcept override { cancelled = true; }
};
struct Uid : aii::voice::SpeakerIdentifier {
  bool anonymous=false;
  std::atomic<bool> release{false},cancelled{false};
  void open() override {release=false;cancelled=false;}
  std::string identify(uint64_t,const std::vector<float>&) override {
    while(!release && !cancelled)std::this_thread::sleep_for(std::chrono::milliseconds(1));
    if(cancelled)throw aii::voice::Cancelled("fixture UID cancelled");
    return R"({"outcome":"known","speaker_id":"person-a","label":"Fixture speaker","reason":"fixture_match"})";
  }
  std::string identify_track_at(uint64_t,uint64_t,const std::vector<float>& pcm) override {
    if(cancelled)throw aii::voice::Cancelled("fixture UID cancelled");
    if(anonymous)return std::string(R"({"outcome":"unavailable","reason":"acoustic_profile_match","speaker_uuid":"12345678-1234-4234-8234-123456789abc","registry_revision":"3","continuity":"matched","policy_sha256":")")+
      std::string(64,'a')+R"(","embedding_binding":")"+std::string(64,'b')+
      R"(","pcm_sha256":")"+std::string(64,'c')+R"(","samples":)"+std::to_string(pcm.size())+"}";
    return std::string(R"({"outcome":"known","speaker_id":"person-a","label":"Fixture speaker","reason":"fixture_match","enrollment_revision":"1","policy_sha256":")")+
      std::string(64,'a')+R"(","embedding_binding":")"+std::string(64,'b')+
      R"(","pcm_sha256":")"+std::string(64,'c')+R"(","samples":)"+std::to_string(pcm.size())+"}";
  }
  void cancel() noexcept override {cancelled=true;}
};
struct Models : aii::voice::ModelOwner {
  Asr a;
  Vad v;
  Endpoint e;
  Tts t;
  Uid u;
  bool uid_enabled;
  explicit Models(bool enabled,bool separated=false,bool anonymous=false) : uid_enabled(enabled) {
    a.separated_track=separated;u.anonymous=anonymous;t.release_uid=&u.release;
  }
  aii::voice::Recognizer &recognizer() override { return a; }
  aii::voice::Vad &vad() override { return v; }
  aii::voice::Endpoint &endpoint() override { return e; }
  aii::voice::Synthesizer &synthesizer() override { return t; }
  aii::voice::SpeakerIdentifier* speaker() override {return uid_enabled?&u:nullptr;}
  void track_observer_at(aii_voice_track_observer_at,void*) override {}
  aii_voice_readiness warm() override { return {uid_enabled?5u:4u, 1, "fixture"}; }
};
}
extern "C" aii_voice_result aii_voice_models_load(const aii_voice_paths *paths,
                                                  aii_voice_models **out,
                                                  aii_voice_error *) {
  const auto mode=paths&&paths->asr?std::string(paths->asr):"";
  *out = aii::voice::wrap_models(std::make_unique<Models>(
      mode=="fixture-uid"||mode=="fixture-separated-uid"||mode=="fixture-separated-anonymous",
      mode=="fixture-separated-uid"||mode=="fixture-separated-anonymous",mode=="fixture-separated-anonymous"));
  return AII_VOICE_OK;
}
extern "C" aii_voice_result aii_voice_models_load_uid_policies(const aii_voice_paths *p, const char *backend,
    const char *, const char *, size_t, aii_voice_snapshot_reader, void *, const char *, const char *, size_t,
    aii_voice_models **out, aii_voice_error *error) {
  if(!backend || std::string(backend)!="cpu")return AII_VOICE_INVALID;
  return aii_voice_models_load(p,out,error);
}
extern "C" aii_voice_result aii_voice_model_server_run(const aii_voice_paths *, const char *, const char *,
    const char *, size_t, const char *, const char *, size_t, uint32_t, uint32_t, uint32_t, uint32_t, aii_voice_model_server_ready,
    void *, intptr_t, intptr_t, aii_voice_error *error) {
  if(error)std::snprintf(error->message,sizeof error->message,"%s","the fixture has no model server");
  return AII_VOICE_INVALID;
}
extern "C" aii_voice_result aii_voice_models_load_channel(intptr_t channel, intptr_t channel_out, const char *, const char *,
    size_t, aii_voice_snapshot_reader, void *, const char *, size_t, aii_voice_models **, aii_voice_error *error) {
#ifndef _WIN32
  if(channel>=0&&channel<=INT32_MAX)::close(int(channel));
  if(channel_out>=0&&channel_out<=INT32_MAX&&channel_out!=channel)::close(int(channel_out));
#else
  if(channel>0)CloseHandle(reinterpret_cast<HANDLE>(channel));
  if(channel_out>0&&channel_out!=channel)CloseHandle(reinterpret_cast<HANDLE>(channel_out));
#endif
  if(error)std::snprintf(error->message,sizeof error->message,"%s","the fixture has no model server");
  return AII_VOICE_INVALID;
}
extern "C" aii_voice_result aii_voice_models_load_with_backend(const aii_voice_paths *p,
    const char *backend, aii_voice_models **out, aii_voice_error *error) {
  if(!backend || std::string(backend)!="cpu")return AII_VOICE_INVALID;
  return aii_voice_models_load(p,out,error);
}
