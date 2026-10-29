#include "model_channel.h"
#include "model_link.h"
#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <cstring>
#include <deque>
#include <map>
#include <mutex>
#include <optional>
#include <thread>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <unistd.h>
#endif

namespace aii::voice {
namespace {
enum Kind : uint8_t { Open = 1, Call = 2, Cancel = 3, Close = 4, Result = 5, Progress = 6, Describe = 7, Admit = 8, Release = 9 };
enum Status : uint8_t { Ok = 0, RuntimeError = 1, InvalidArgument = 2, CancelledCall = 3, LogicError = 4 };
enum SpeechCall : uint8_t { SpeechOpen = 1, SpeechReserve = 2, SpeechConfigure = 3, SpeechCheck = 4,
                            SpeechStart = 5, SpeechNext = 6, SpeechReset = 7 };
enum HearingCall : uint8_t { HearingOpen = 1, HearingBegin = 2, HearingPush = 3, HearingFinish = 4,
                             HearingOnset = 7, HearingSegments = 8, HearingPrefer = 9, HearingBound = 10,
                             HearingReset = 11, HearingExecution = 12 };
enum EndpointCall : uint8_t { EndpointOpen = 1, EndpointScore = 2 };
enum SpeakerCall : uint8_t { SpeakerEmbed = 1, SpeakerResume = 2 };
constexpr uint32_t frame_limit = 64u << 20;
constexpr size_t header_bytes = 1 + 8 + 8;
constexpr size_t stream_limit = 16;

struct Frame { uint8_t kind = 0; uint64_t request = 0, stream = 0; std::string payload; };

std::optional<Frame> read_frame(Link& link) {
  uint32_t length = 0;
  if (!link.read_all(&length, sizeof length) || length < header_bytes || length > frame_limit) return std::nullopt;
  std::string raw(length, '\0');
  if (!link.read_all(raw.data(), raw.size())) return std::nullopt;
  Frame f;
  f.kind = uint8_t(raw[0]);
  std::memcpy(&f.request, raw.data() + 1, 8);
  std::memcpy(&f.stream, raw.data() + 9, 8);
  f.payload = raw.substr(header_bytes);
  return f;
}
std::string encode_frame(const Frame& f) {
  const uint32_t length = uint32_t(header_bytes + f.payload.size());
  std::string raw(sizeof length + length, '\0');
  std::memcpy(raw.data(), &length, sizeof length);
  raw[4] = char(f.kind);
  std::memcpy(raw.data() + 5, &f.request, 8);
  std::memcpy(raw.data() + 13, &f.stream, 8);
  std::memcpy(raw.data() + 21, f.payload.data(), f.payload.size());
  return raw;
}

constexpr size_t outbox_limit = 2 * size_t(frame_limit);
class Outbox {
 public:
  explicit Outbox(Link& link) : link_(link) { thread_ = std::thread([this] { run(); }); }
  ~Outbox() { stop(); }
  Outbox(const Outbox&) = delete;
  Outbox& operator=(const Outbox&) = delete;
  bool post(const Frame& f) noexcept {
    try {
      if (f.payload.size() > frame_limit - header_bytes) return false;
      auto raw = encode_frame(f);
      std::lock_guard<std::mutex> lock(mutex_);
      if (stopped_) return false;
      if (bytes_ + raw.size() > outbox_limit) { break_locked(); return false; }
      bytes_ += raw.size();
      queue_.push_back(std::move(raw));
      changed_.notify_one();
      return true;
    } catch (...) {
      return false;
    }
  }
  void stop() noexcept {
    { std::lock_guard<std::mutex> lock(mutex_); stopped_ = true; }
    changed_.notify_all();
    if (!thread_.joinable()) return;
    link_.end_writing();
    thread_.join();
  }
 private:
  void break_locked() {
    stopped_ = true;
    queue_.clear();
    link_.end();
    changed_.notify_all();
  }
  void run() {
    for (;;) {
      std::string raw;
      {
        std::unique_lock<std::mutex> lock(mutex_);
        changed_.wait(lock, [&] { return stopped_ || !queue_.empty(); });
        if (stopped_) return;
        raw = std::move(queue_.front());
        queue_.pop_front();
      }
      const bool written = link_.write_all(raw.data(), raw.size());
      std::lock_guard<std::mutex> lock(mutex_);
      bytes_ -= std::min(bytes_, raw.size());
      if (!written) { break_locked(); return; }
    }
  }
  Link& link_;
  std::mutex mutex_;
  std::condition_variable changed_;
  std::deque<std::string> queue_;
  size_t bytes_ = 0;
  bool stopped_ = false;
  std::thread thread_;
};

struct Writer {
  std::string out;
  template <class T> void put(T v) { out.append(reinterpret_cast<const char*>(&v), sizeof v); }
  void str(const std::string& s) { put(uint32_t(s.size())); out += s; }
  void floats(const float* p, size_t n) { put(uint32_t(n)); out.append(reinterpret_cast<const char*>(p), n * sizeof(float)); }
  void floats(const std::vector<float>& v) { floats(v.data(), v.size()); }
  void doubles(const std::vector<double>& v) { put(uint32_t(v.size())); out.append(reinterpret_cast<const char*>(v.data()), v.size() * sizeof(double)); }
};
struct Reader {
  const std::string& in;
  size_t at = 0;
  void need(size_t n) const { if (in.size() - at < n) throw std::runtime_error("model channel: a frame is shorter than its contents"); }
  template <class T> T get() { need(sizeof(T)); T v; std::memcpy(&v, in.data() + at, sizeof v); at += sizeof v; return v; }
  std::string str() { const auto n = get<uint32_t>(); need(n); auto s = in.substr(at, n); at += n; return s; }
  std::vector<float> floats() {
    const auto n = get<uint32_t>(); need(size_t(n) * sizeof(float));
    std::vector<float> v(n); std::memcpy(v.data(), in.data() + at, size_t(n) * sizeof(float)); at += size_t(n) * sizeof(float);
    return v;
  }
  std::vector<double> doubles() {
    const auto n = get<uint32_t>(); need(size_t(n) * sizeof(double));
    std::vector<double> v(n); std::memcpy(v.data(), in.data() + at, size_t(n) * sizeof(double)); at += size_t(n) * sizeof(double);
    return v;
  }
};
void put_settings(Writer& w, const SpeechSettings& s) {
  w.str(s.voice); w.str(s.tts_language); w.str(s.stt_language); w.put(s.temperature); w.put(s.seed);
}
SpeechSettings get_settings(Reader& r) {
  SpeechSettings s;
  s.voice = r.str(); s.tts_language = r.str(); s.stt_language = r.str();
  s.temperature = r.get<float>(); s.seed = r.get<uint32_t>();
  return s;
}
void put_segments(Writer& w, const std::vector<RecognizedSegment>& segments) {
  w.put(uint32_t(segments.size()));
  for (const auto& s : segments) {
    w.str(s.track); w.str(s.text); w.put(s.start); w.put(s.end); w.put(s.evidence_start); w.floats(s.evidence);
    w.put(uint32_t(s.evidence_regions.size()));
    for (const auto& [from, to] : s.evidence_regions) { w.put(from); w.put(to); }
    w.str(s.evidence_unavailable);
  }
}
std::vector<RecognizedSegment> get_segments(Reader& r) {
  std::vector<RecognizedSegment> segments(r.get<uint32_t>());
  for (auto& s : segments) {
    s.track = r.str(); s.text = r.str(); s.start = r.get<uint64_t>(); s.end = r.get<uint64_t>();
    s.evidence_start = r.get<uint64_t>(); s.evidence = r.floats();
    s.evidence_regions.resize(r.get<uint32_t>());
    for (auto& region : s.evidence_regions) { region.first = r.get<uint64_t>(); region.second = r.get<uint64_t>(); }
    s.evidence_unavailable = r.str();
  }
  return segments;
}

struct ServedStream {
  StreamKind kind = StreamKind::Speech;
  std::thread thread;
  std::mutex mutex;
  std::condition_variable changed;
  std::deque<Frame> tasks;
  std::unique_ptr<Synthesizer> speech;
  std::unique_ptr<Recognizer> hearing;
  std::unique_ptr<Endpoint> endpoint;
  std::unique_ptr<SpeakerEmbedder> speaker;
  std::atomic<Synthesizer*> live_speech{nullptr};
  std::atomic<Recognizer*> live_hearing{nullptr};
  std::atomic<Endpoint*> live_endpoint{nullptr};
  std::atomic<SpeakerEmbedder*> live_speaker{nullptr};
  std::atomic<bool> done{false};
  std::condition_variable retired;
  void free() {
    live_speech = nullptr; live_hearing = nullptr; live_endpoint = nullptr; live_speaker = nullptr;
    speech.reset(); hearing.reset(); endpoint.reset(); speaker.reset();
  }
  void cancel(uint64_t argument) noexcept {
    if (auto* s = live_speech.load()) s->cancel(argument);
    if (auto* h = live_hearing.load()) h->cancel();
    if (auto* e = live_endpoint.load()) e->cancel();
    if (auto* k = live_speaker.load()) k->cancel();
  }
};
std::atomic<size_t> unjoined{0};
std::atomic<uint64_t> holders{0};

bool immediate(StreamKind kind, const std::string& payload) {
  if (payload.empty()) return false;
  const auto call = uint8_t(payload[0]);
  return (kind == StreamKind::Speech && (call == SpeechCheck || call == SpeechReserve)) ||
         (kind == StreamKind::Hearing && call == HearingExecution) ||
         (kind == StreamKind::Speaker && call == SpeakerResume);
}

using ProgressSender = std::function<void()>;
std::string execute(StreamFactory& factory, ServedStream& s, const Frame& task, const ProgressSender& progress) {
  Writer w;
  if (task.kind == Open) {
    switch (s.kind) {
      case StreamKind::Speech: s.speech = factory.speech(); s.live_speech = s.speech.get(); break;
      case StreamKind::Hearing:
        s.hearing = factory.recognizer(); s.live_hearing = s.hearing.get();
        w.put(uint8_t(s.hearing->separated())); w.put(uint8_t(s.hearing->continuous_input()));
        break;
      case StreamKind::Endpoint: s.endpoint = factory.endpoint(); s.live_endpoint = s.endpoint.get(); break;
      case StreamKind::Speaker: s.speaker = factory.speaker(); s.live_speaker = s.speaker.get(); break;
      default: throw std::invalid_argument("model channel: unknown stream kind");
    }
    return w.out;
  }
  if (task.kind == Close) { s.free(); return {}; }
  Reader r{task.payload};
  const auto call = r.get<uint8_t>();
  if (s.speech) {
    auto& m = *s.speech;
    switch (call) {
      case SpeechOpen: m.open(); break;
      case SpeechConfigure: m.configure(get_settings(r)); break;
      case SpeechStart: { const auto id = r.get<uint64_t>(); const auto text = r.str(); m.start(id, text); w.put(uint8_t(m.silent())); break; }
      case SpeechNext: w.floats(m.next()); break;
      case SpeechReset: m.reset(); break;
      default: throw std::invalid_argument("model channel: unknown speaking call");
    }
  } else if (s.hearing) {
    auto& m = *s.hearing;
    switch (call) {
      case HearingOpen: m.open(); break;
      case HearingBegin: m.begin(); break;
      case HearingPush: { const auto pcm = r.floats(); w.str(m.push(pcm.data(), pcm.size())); break; }
      case HearingFinish: w.str(m.finish_with_progress(progress)); break;
      case HearingOnset: m.speech_onset(r.get<uint64_t>()); break;
      case HearingSegments: put_segments(w, m.segments()); break;
      case HearingPrefer: {
        std::vector<std::string> terms(r.get<uint32_t>());
        for (auto& t : terms) t = r.str();
        w.put(uint64_t(m.prefer(terms)));
        break;
      }
      case HearingBound: {
        const auto minimum = r.get<uint32_t>(), maximum = r.get<uint32_t>(), call_ms = r.get<uint32_t>();
        m.bound_separation(minimum, maximum, call_ms);
        break;
      }
      case HearingReset: m.reset(); break;
      default: throw std::invalid_argument("model channel: unknown hearing call");
    }
  } else if (s.endpoint) {
    auto& m = *s.endpoint;
    switch (call) {
      case EndpointOpen: m.open(); break;
      case EndpointScore: { const auto id = r.get<uint64_t>(); const auto pcm = r.floats(); w.put(m.score(id, pcm)); break; }
      default: throw std::invalid_argument("model channel: unknown endpoint call");
    }
  } else if (s.speaker) {
    if (call != SpeakerEmbed) throw std::invalid_argument("model channel: unknown speaker call");
    const auto dimensions = r.get<uint32_t>();
    const auto pcm = r.str();
    w.doubles(s.speaker->embed(std::vector<uint8_t>(pcm.begin(), pcm.end()), dimensions));
  } else {
    throw std::logic_error("model channel: the stream is not open");
  }
  return w.out;
}
std::string answer_now(ServedStream& s, const std::string& payload) {
  Reader r{payload};
  const auto call = r.get<uint8_t>();
  Writer w;
  if (s.kind == StreamKind::Speech) {
    auto* live = s.live_speech.load();
    if (!live) throw std::logic_error("model channel: the stream is not open");
    if (call == SpeechCheck) live->check(get_settings(r));
    else w.put(uint64_t(live->playback_reserve_samples()));
  } else if (s.kind == StreamKind::Speaker) {
    auto* live = s.live_speaker.load();
    if (!live) throw std::logic_error("model channel: the stream is not open");
    live->resume();
  } else {
    auto* live = s.live_hearing.load();
    if (!live) throw std::logic_error("model channel: the stream is not open");
    w.str(live->execution_info());
  }
  return w.out;
}
template <class F> std::pair<uint8_t, std::string> answered(F f) {
  try { return {Ok, f()}; }
  catch (const Cancelled& e) { return {CancelledCall, e.what()}; }
  catch (const std::invalid_argument& e) { return {InvalidArgument, e.what()}; }
  catch (const std::logic_error& e) { return {LogicError, e.what()}; }
  catch (const std::exception& e) { return {RuntimeError, e.what()}; }
}
}

void serve_model_channel(StreamFactory& factory, ChannelEnd end, std::chrono::milliseconds retire, HungStream hung) {
  Link link(end);
  serve_model_channel(factory, link, retire, std::move(hung));
}
void serve_model_channel(StreamFactory& factory, Link& link, std::chrono::milliseconds retire, HungStream hung) {
  const auto out = std::make_shared<Outbox>(link);
  const auto send = [out](const Frame& f) { (void)out->post(f); };
  const auto answer = [send](const Frame& task, uint8_t status, const std::string& body) {
    send(Frame{Result, task.request, task.stream, std::string(1, char(status)) + body});
  };
  StreamFactory* const made_by = &factory;
  const auto run = [send, answer, made_by](ServedStream& s) {
    for (;;) {
      Frame task;
      {
        std::unique_lock<std::mutex> lock(s.mutex);
        s.changed.wait(lock, [&] { return !s.tasks.empty(); });
        task = std::move(s.tasks.front());
        s.tasks.pop_front();
      }
      const ProgressSender progress = [&] { send(Frame{Progress, task.request, task.stream, {}}); };
      const auto [status, body] = answered([&] { return execute(*made_by, s, task, progress); });
      if (task.request) answer(task, status, body);
      if (task.kind == Close) {
        { std::lock_guard<std::mutex> lock(s.mutex); s.done = true; }
        s.retired.notify_all();
        return;
      }
    }
  };
  const auto enqueue = [](ServedStream& s, Frame f) {
    { std::lock_guard<std::mutex> lock(s.mutex); s.tasks.push_back(std::move(f)); }
    s.changed.notify_one();
  };
  std::map<uint64_t, std::unique_ptr<ServedStream>> streams;
  std::deque<std::unique_ptr<ServedStream>> closed;
  const auto reap = [&] {
    for (auto it = closed.begin(); it != closed.end();) {
      if ((*it)->done) { if ((*it)->thread.joinable()) { (*it)->thread.join(); --unjoined; } it = closed.erase(it); }
      else ++it;
    }
  };
  uint64_t next_stream = 0;
  const uint64_t holder = ++holders;
  while (auto frame = read_frame(link)) {
    reap();
    if (frame->kind == Describe) {
      const auto [status, body] = answered([&] { return factory.describe(); });
      answer(*frame, status, body);
      continue;
    }
    if (frame->kind == Admit || frame->kind == Release) {
      const auto [status, body] = answered([&] {
        Writer w;
        auto* admission = factory.admission();
        if (frame->kind == Release) {
          if (admission) admission->release(holder);
          return w.out;
        }
        const auto given = admission ? admission->admit(holder) : Admission::Answer{true, 0, 0};
        w.put(uint8_t(given.admitted)); w.put(given.retry_after_ms); w.put(given.held);
        return w.out;
      });
      answer(*frame, status, body);
      continue;
    }
    if (frame->kind == Open) {
      if (streams.size() >= stream_limit) {
        answer(*frame, RuntimeError, "model channel: at most 16 streams on one channel");
        continue;
      }
      const auto kind = frame->payload.empty() ? 0 : uint8_t(frame->payload[0]);
      if (kind < uint8_t(StreamKind::Speech) || kind > uint8_t(StreamKind::Speaker)) {
        answer(*frame, InvalidArgument, "model channel: unknown stream kind");
        continue;
      }
      frame->stream = ++next_stream;
      auto s = std::make_unique<ServedStream>();
      s->kind = StreamKind(kind);
      auto* raw = s.get();
      streams[frame->stream] = std::move(s);
      enqueue(*raw, std::move(*frame));
      raw->thread = std::thread([run, raw] { run(*raw); });
      ++unjoined;
      continue;
    }
    const auto found = streams.find(frame->stream);
    if (found == streams.end()) {
      if (frame->kind != Cancel) answer(*frame, InvalidArgument, "model channel: unknown stream");
      continue;
    }
    auto& s = *found->second;
    if (frame->kind == Cancel) {
      try { Reader r{frame->payload}; s.cancel(r.get<uint64_t>()); } catch (...) {}
      continue;
    }
    if (frame->kind == Call && immediate(s.kind, frame->payload)) {
      const auto [status, body] = answered([&] { return answer_now(s, frame->payload); });
      answer(*frame, status, body);
      continue;
    }
    const bool closing = frame->kind == Close;
    enqueue(s, std::move(*frame));
    if (closing) { closed.push_back(std::move(found->second)); streams.erase(found); }
  }
  if (auto* admission = factory.admission()) admission->release(holder);
  for (auto& [id, s] : streams) {
    s->cancel(UINT64_MAX);
    enqueue(*s, Frame{Close, 0, id, {}});
    closed.push_back(std::move(s));
  }
  if (retire.count() > 0) {
    const auto until = std::chrono::steady_clock::now() + retire;
    for (auto& s : closed) {
      bool done;
      {
        std::unique_lock<std::mutex> lock(s->mutex);
        done = s->retired.wait_until(lock, until, [&] { return s->done.load(); });
      }
      if (done) continue;
      if (hung) hung("a stream of kind " + std::to_string(int(s->kind)) + " did not retire within " +
                     std::to_string(retire.count()) + " ms of its channel's end");
      s->thread.detach();
      (void)s.release();
    }
  }
  for (auto& s : closed) if (s && s->thread.joinable()) { s->thread.join(); --unjoined; }
  link.end();
  out->stop();
}
size_t model_channel_unjoined_threads() { return unjoined; }

struct ModelChannel::Impl {
  struct Waiting { std::optional<Frame> reply; size_t progress = 0; };
  ChannelEnd ends;
  std::unique_ptr<Link> link;
  std::mutex mutex;
  std::condition_variable changed;
  std::map<uint64_t, Waiting> waiting;
  uint64_t next_request = 0;
  bool lost = false;
  std::thread reader;
  std::unique_ptr<Outbox> box;
  explicit Impl(ChannelEnd end) : ends(end) {
    try {
      link = std::make_unique<Link>(end);
    } catch (...) {
      close_ends();
      throw;
    }
    box = std::make_unique<Outbox>(*link);
    reader = std::thread([this] {
      for (;;) {
        auto f = read_frame(*link);
        std::lock_guard<std::mutex> lock(mutex);
        if (!f) { lost = true; changed.notify_all(); return; }
        const auto found = waiting.find(f->request);
        if (found != waiting.end()) {
          if (f->kind == Progress) ++found->second.progress;
          else found->second.reply = std::move(*f);
        }
        changed.notify_all();
      }
    });
  }
  ~Impl() {
    link->end();
    box->stop();
    if (reader.joinable()) reader.join();
    box.reset();
    link.reset();
    close_ends();
  }
  void close_ends() noexcept { close_channel_end(ends); }
  Frame exchange(Frame f, const std::function<void()>& progress = {}) {
    {
      std::lock_guard<std::mutex> lock(mutex);
      if (lost) throw std::runtime_error("model server lost");
      f.request = ++next_request;
      waiting.emplace(f.request, Waiting{});
    }
    const bool sent = box->post(f);
    std::unique_lock<std::mutex> lock(mutex);
    while (sent) {
      changed.wait(lock, [&] { auto& w = waiting[f.request]; return lost || w.reply.has_value() || w.progress; });
      auto& w = waiting[f.request];
      if (w.progress) {
        --w.progress;
        lock.unlock();
        try { if (progress) progress(); }
        catch (...) { cancel(f.stream, 0); lock.lock(); waiting.erase(f.request); throw; }
        lock.lock();
        continue;
      }
      break;
    }
    auto reply = std::move(waiting[f.request].reply);
    waiting.erase(f.request);
    if (!reply) throw std::runtime_error("model server lost");
    return std::move(*reply);
  }
  static std::string body(const Frame& reply) {
    if (reply.payload.empty()) throw std::runtime_error("model channel: an answer without a status");
    const auto status = uint8_t(reply.payload[0]);
    auto text = reply.payload.substr(1);
    switch (status) {
      case Ok: return text;
      case CancelledCall: throw Cancelled(text);
      case InvalidArgument: throw std::invalid_argument(text);
      case LogicError: throw std::logic_error(text);
      default: throw std::runtime_error(text);
    }
  }
  std::pair<uint64_t, std::string> open(StreamKind kind) {
    const auto reply = exchange(Frame{Open, 0, 0, std::string(1, char(kind))});
    auto opened = body(reply);
    return {reply.stream, std::move(opened)};
  }
  std::string call(uint64_t stream, uint8_t method, const std::string& args, const std::function<void()>& progress = {}) {
    return body(exchange(Frame{Call, 0, stream, std::string(1, char(method)) + args}, progress));
  }
  void cancel(uint64_t stream, uint64_t argument) noexcept {
    try {
      Writer w; w.put(argument);
      (void)box->post(Frame{Cancel, 0, stream, w.out});
    } catch (...) {}
  }
  void close(uint64_t stream) noexcept {
    try { body(exchange(Frame{Close, 0, stream, {}})); } catch (...) {}
  }
};

namespace {
using Channel = std::shared_ptr<ModelChannel::Impl>;
struct RemoteSynthesizer final : Synthesizer {
  Channel channel;
  uint64_t stream;
  bool wordless = false;
  explicit RemoteSynthesizer(Channel c) : channel(std::move(c)), stream(channel->open(StreamKind::Speech).first) {}
  ~RemoteSynthesizer() override { channel->close(stream); }
  std::string invoke(uint8_t method, const Writer& args = {}) const { return channel->call(stream, method, args.out); }
  void open() override { invoke(SpeechOpen); }
  size_t playback_reserve_samples() const override {
    const auto b = invoke(SpeechReserve); Reader r{b}; return size_t(r.get<uint64_t>());
  }
  void configure(const SpeechSettings& s) override { Writer w; put_settings(w, s); invoke(SpeechConfigure, w); }
  void check(const SpeechSettings& s) const override { Writer w; put_settings(w, s); invoke(SpeechCheck, w); }
  void start(uint64_t id, const std::string& text) override {
    Writer w; w.put(id); w.str(text);
    const auto b = invoke(SpeechStart, w); Reader r{b}; wordless = r.get<uint8_t>() != 0;
  }
  bool silent() const override { return wordless; }
  std::vector<float> next() override { const auto b = invoke(SpeechNext); Reader r{b}; return r.floats(); }
  void reset() override { invoke(SpeechReset); }
  void cancel(uint64_t id) noexcept override { channel->cancel(stream, id); }
};
struct RemoteRecognizer final : Recognizer {
  Channel channel;
  uint64_t stream = 0;
  bool is_separated = false, is_continuous = false;
  explicit RemoteRecognizer(Channel c) : channel(std::move(c)) {
    auto opened = channel->open(StreamKind::Hearing);
    stream = opened.first;
    Reader r{opened.second};
    is_separated = r.get<uint8_t>() != 0; is_continuous = r.get<uint8_t>() != 0;
  }
  ~RemoteRecognizer() override { channel->close(stream); }
  std::string invoke(uint8_t method, const Writer& args = {}, const std::function<void()>& progress = {}) const {
    return channel->call(stream, method, args.out, progress);
  }
  std::string execution_info() const override { const auto b = invoke(HearingExecution); Reader r{b}; return r.str(); }
  void open() override { invoke(HearingOpen); }
  void begin() override { invoke(HearingBegin); }
  std::string push(const float* pcm, size_t n) override {
    Writer w; w.floats(pcm, n); const auto b = invoke(HearingPush, w); Reader r{b}; return r.str();
  }
  std::string finish() override { return finish_with_progress({}); }
  std::string finish_with_progress(const std::function<void()>& progress) override {
    const auto b = invoke(HearingFinish, {}, progress); Reader r{b}; return r.str();
  }
  bool separated() const override { return is_separated; }
  bool continuous_input() const override { return is_continuous; }
  void speech_onset(uint64_t at) override { Writer w; w.put(at); invoke(HearingOnset, w); }
  std::vector<RecognizedSegment> segments() const override { const auto b = invoke(HearingSegments); Reader r{b}; return get_segments(r); }
  size_t prefer(const std::vector<std::string>& terms) override {
    Writer w; w.put(uint32_t(terms.size())); for (const auto& t : terms) w.str(t);
    const auto b = invoke(HearingPrefer, w); Reader r{b}; return size_t(r.get<uint64_t>());
  }
  void bound_separation(uint32_t minimum, uint32_t maximum, uint32_t call_ms) override {
    Writer w; w.put(minimum); w.put(maximum); w.put(call_ms); invoke(HearingBound, w);
  }
  void reset() override { invoke(HearingReset); }
  void cancel() noexcept override { channel->cancel(stream, 0); }
};
struct RemoteEndpoint final : Endpoint {
  Channel channel;
  uint64_t stream;
  explicit RemoteEndpoint(Channel c) : channel(std::move(c)), stream(channel->open(StreamKind::Endpoint).first) {}
  ~RemoteEndpoint() override { channel->close(stream); }
  void open() override { channel->call(stream, EndpointOpen, {}); }
  double score(uint64_t id, const std::vector<float>& pcm) override {
    Writer w; w.put(id); w.floats(pcm);
    const auto b = channel->call(stream, EndpointScore, w.out); Reader r{b}; return r.get<double>();
  }
  void cancel() noexcept override { channel->cancel(stream, 0); }
};
struct RemoteEmbedder final : SpeakerEmbedder {
  Channel channel;
  uint64_t stream;
  explicit RemoteEmbedder(Channel c) : channel(std::move(c)), stream(channel->open(StreamKind::Speaker).first) {}
  ~RemoteEmbedder() override { channel->close(stream); }
  aii::uid::Vector embed(const std::vector<uint8_t>& pcm, size_t dimensions) override {
    if (dimensions > UINT32_MAX) throw std::invalid_argument("model channel: embedding dimensions out of range");
    Writer w; w.put(uint32_t(dimensions)); w.str(std::string(pcm.begin(), pcm.end()));
    const auto b = channel->call(stream, SpeakerEmbed, w.out); Reader r{b};
    auto embedding = r.doubles();
    if (embedding.size() != dimensions) throw std::runtime_error("model channel: embedding dimensions differ");
    return embedding;
  }
  void cancel() noexcept override { channel->cancel(stream, 0); }
  void resume() override { channel->call(stream, SpeakerResume, {}); }
};
}

ModelChannel::ModelChannel(ChannelEnd end) : p_(std::make_shared<Impl>(end)) {}
ModelChannel::~ModelChannel() = default;
std::unique_ptr<Synthesizer> ModelChannel::speech() { return std::make_unique<RemoteSynthesizer>(p_); }
std::unique_ptr<Recognizer> ModelChannel::recognizer() { return std::make_unique<RemoteRecognizer>(p_); }
std::unique_ptr<Endpoint> ModelChannel::endpoint() { return std::make_unique<RemoteEndpoint>(p_); }
std::unique_ptr<SpeakerEmbedder> ModelChannel::speaker() { return std::make_unique<RemoteEmbedder>(p_); }
std::string ModelChannel::describe() { return p_->body(p_->exchange(Frame{Describe, 0, 0, {}})); }
bool ModelChannel::lost() const { std::lock_guard<std::mutex> lock(p_->mutex); return p_->lost; }
Admission::Answer ModelChannel::admit() {
  const auto b = p_->body(p_->exchange(Frame{Admit, 0, 0, {}}));
  Reader r{b};
  Admission::Answer a;
  a.admitted = r.get<uint8_t>() != 0; a.retry_after_ms = r.get<uint32_t>(); a.held = r.get<uint32_t>();
  return a;
}
void ModelChannel::release() { p_->body(p_->exchange(Frame{Release, 0, 0, {}})); }
}
