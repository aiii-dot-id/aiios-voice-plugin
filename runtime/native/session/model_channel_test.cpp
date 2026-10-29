#include "model_channel.h"
#include "model_link.h"
#include <atomic>
#include <chrono>
#include <cstdio>
#include <csignal>
#include <future>
#include <thread>
#include <fcntl.h>
#include <unistd.h>
using namespace aii::voice;
using Clock = std::chrono::steady_clock;
namespace {
int failed = 0;
void check(bool ok, const char* why) { if (!ok) { std::fprintf(stderr, "FAILED: %s\n", why); ++failed; } }
double ms(Clock::time_point since) { return std::chrono::duration<double, std::milli>(Clock::now() - since).count(); }
std::atomic<int> destroyed{0};
std::atomic<bool> slow{false};
std::atomic<int> fake_stages{3};
std::atomic<bool> deaf{false};

struct FakeSpeech final : Synthesizer {
  std::string text; uint64_t id = 0; int chunk = 0; std::atomic<bool> cancelled{false};
  ~FakeSpeech() override { ++destroyed; }
  void configure(const SpeechSettings& s) override { if (s.voice == "bad") throw std::invalid_argument("no such voice"); }
  void check(const SpeechSettings& s) const override { if (s.voice == "bad") throw std::invalid_argument("no such voice"); }
  size_t playback_reserve_samples() const override { return 4800; }
  void start(uint64_t generation, const std::string& t) override { id = generation; text = t; chunk = 0; cancelled = false; }
  std::vector<float> next() override {
    if (deaf) std::this_thread::sleep_for(std::chrono::milliseconds(1500));
    const auto begun = Clock::now();
    while (slow && !cancelled && ms(begun) < 2000) std::this_thread::sleep_for(std::chrono::milliseconds(2));
    if (cancelled) throw Cancelled("synthesis cancelled during inference");
    if (chunk == 3 || silent()) return {};
    std::vector<float> pcm;
    for (unsigned char c : text) pcm.push_back(float(c) / 256.f + float(chunk) + float(id) / 1000.f);
    ++chunk;
    return pcm;
  }
  bool silent() const override { return text == "()"; }
  void reset() override {}
  void cancel(uint64_t generation) noexcept override { if (generation == id) cancelled = true; }
};
struct Factory final : StreamFactory {
  std::unique_ptr<Synthesizer> speech() override { return std::make_unique<FakeSpeech>(); }
  std::unique_ptr<Recognizer> recognizer() override;
  std::unique_ptr<Endpoint> endpoint() override;
  std::unique_ptr<SpeakerEmbedder> speaker() override;
};
std::vector<float> speak(Synthesizer& s, uint64_t id, const std::string& text) {
  SpeechSettings settings; s.open(); s.configure(settings); s.start(id, text);
  std::vector<float> all;
  for (;;) { auto part = s.next(); if (part.empty()) break; all.insert(all.end(), part.begin(), part.end()); }
  s.reset();
  return all;
}
struct FakeHearing final : Recognizer {
  std::atomic<bool> cancelled{false};
  size_t pushed = 0;
  std::string execution_info() const override { return "{\"encoder_provider\":\"fake\"}"; }
  void begin() override { pushed = 0; cancelled = false; }
  std::string push(const float* pcm, size_t n) override {
    const auto begun = Clock::now();
    while (slow && !cancelled && ms(begun) < 2000) std::this_thread::sleep_for(std::chrono::milliseconds(2));
    if (cancelled) throw Cancelled("recognition cancelled");
    float sum = 0; for (size_t i = 0; i < n; ++i) sum += pcm[i];
    pushed += n; return "partial " + std::to_string(pushed) + " " + std::to_string(sum);
  }
  std::string finish() override { return "final " + std::to_string(pushed); }
  std::string finish_with_progress(const std::function<void()>& done) override {
    for (int stage = 0; stage < fake_stages; ++stage) {
      if (cancelled) throw Cancelled("recognition cancelled between stages");
      if (slow) std::this_thread::sleep_for(std::chrono::milliseconds(100));
      if (done) done();
    }
    return finish();
  }
  bool separated() const override { return true; }
  bool continuous_input() const override { return false; }
  std::vector<RecognizedSegment> segments() const override {
    RecognizedSegment a; a.track = "speaker-0"; a.text = "hello"; a.start = 10; a.end = 4000; a.evidence_start = 7;
    a.evidence = {0.25f, -0.5f, 0.125f}; a.evidence_regions = {{1, 2}, {30, 4000}};
    RecognizedSegment b; b.track = "speaker-1"; b.text = "there"; b.evidence_unavailable = "too_short";
    return {a, b};
  }
  size_t prefer(const std::vector<std::string>& terms) override { return terms.size(); }
  void reset() override {}
  void cancel() noexcept override { cancelled = true; }
};
struct FakeEndpoint final : Endpoint {
  double score(uint64_t id, const std::vector<float>& pcm) override { return double(id) / 10.0 + double(pcm.size()) / 1e6; }
  void cancel() noexcept override {}
};
struct FakeEmbedder final : SpeakerEmbedder {
  std::atomic<bool> cancelled{false};
  aii::uid::Vector embed(const std::vector<uint8_t>& pcm, size_t dimensions) override {
    const auto begun = Clock::now();
    while (slow && !cancelled && ms(begun) < 2000) std::this_thread::sleep_for(std::chrono::milliseconds(2));
    if (cancelled) throw Cancelled("UID cancelled");
    double sum = 0; for (auto b : pcm) sum += b;
    aii::uid::Vector v(dimensions); for (size_t i = 0; i < dimensions; ++i) v[i] = sum / double(i + 1) + 1e-9 * double(i);
    return v;
  }
  void cancel() noexcept override { cancelled = true; }
  void resume() override { cancelled = false; }
};
std::unique_ptr<SpeakerEmbedder> Factory::speaker() { return std::make_unique<FakeEmbedder>(); }
std::unique_ptr<Recognizer> Factory::recognizer() { return std::make_unique<FakeHearing>(); }
std::unique_ptr<Endpoint> Factory::endpoint() { return std::make_unique<FakeEndpoint>(); }
void lose(const ChannelEnd& end) {
  const int null = ::open("/dev/null", O_WRONLY);
  (void)::dup2(null, int(end.out)); ::close(null);
}
}

int main() {
  std::signal(SIGPIPE, SIG_IGN);
  Factory factory;
  const auto ends = model_channel_pipes();
  std::thread server([&] { serve_model_channel(factory, ends.second); close_channel_end(ends.second); });
  {
    ModelChannel channel(ends.first);
    FakeSpeech direct;
    const auto alone_a = speak(direct, 7, "first voice");
    auto remote = channel.speech();
    check(speak(*remote, 7, "first voice") == alone_a, "remote speech differs from in-process speech");
    check(remote->playback_reserve_samples() == 4800, "a constant of the backend did not cross");
    check(speak(*remote, 8, "()").empty() && remote->silent(), "a silent segment was not said silent across the channel");
    check(speak(*remote, 7, "first voice") == alone_a && !remote->silent(), "a spoken segment after a silent one was said silent");
    SpeechSettings bad; bad.voice = "bad";
    bool invalid = false;
    try { remote->configure(bad); } catch (const std::invalid_argument&) { invalid = true; } catch (...) {}
    check(invalid, "invalid_argument did not cross as itself");
    FakeSpeech direct_b;
    const auto alone_b = speak(direct_b, 9, "second voice, a little longer");
    auto a = channel.speech(), b = channel.speech();
    std::vector<float> together_a, together_b;
    std::thread ta([&] { for (int i = 0; i < 50; ++i) together_a = speak(*a, 7, "first voice"); });
    std::thread tb([&] { for (int i = 0; i < 50; ++i) together_b = speak(*b, 9, "second voice, a little longer"); });
    ta.join(); tb.join();
    check(together_a == alone_a && together_b == alone_b, "two streams at once differ from each alone");
    slow = true;
    SpeechSettings settings; a->open(); a->configure(settings); a->start(11, "held");
    auto held = std::async(std::launch::async, [&] { try { a->next(); return false; } catch (const Cancelled&) { return true; } });
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    auto began = Clock::now();
    a->check(settings);
    check(ms(began) < 100, "check() waited behind a running next()");
    began = Clock::now();
    a->cancel(11);
    check(held.get(), "a cancelled call did not end as Cancelled");
    check(ms(began) < 300, "a cancel waited behind the running call");
    slow = false;
    a->reset();
    check(speak(*b, 9, "second voice, a little longer") == alone_b, "another stream was disturbed by a cancel");
    {
      FakeHearing local;
      auto heard = channel.recognizer();
      check(heard->separated() && !heard->continuous_input(), "the composition's constants did not cross");
      check(heard->prefer({"Ivy", "Fern"}) == 2, "prefer did not cross");
      const std::vector<float> block(512, 0.25f);
      heard->begin(); local.begin();
      check(heard->push(block.data(), block.size()) == local.push(block.data(), block.size()), "a push's answer differs");
      const auto caller = std::this_thread::get_id();
      int stages = 0; bool on_caller = true;
      const auto text = heard->finish_with_progress([&] { ++stages; on_caller = on_caller && std::this_thread::get_id() == caller; });
      check(text == local.finish() && stages == 3 && on_caller, "a finish's stages were not all reported, before it returned, on the calling thread");
      const auto got = heard->segments(), want = local.segments();
      check(got.size() == 2 && got[0].track == want[0].track && got[0].text == want[0].text && got[0].start == want[0].start &&
            got[0].end == want[0].end && got[0].evidence_start == want[0].evidence_start && got[0].evidence == want[0].evidence &&
            got[0].evidence_regions == want[0].evidence_regions && got[1].evidence_unavailable == "too_short",
            "segments did not cross whole");
      slow = true;
      heard->begin();
      auto pushing = std::async(std::launch::async, [&] {
        try { heard->push(block.data(), block.size()); return false; } catch (const Cancelled&) { return true; }
      });
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
      auto began = Clock::now();
      check(heard->execution_info() == local.execution_info(), "execution_info did not cross");
      check(ms(began) < 100, "execution_info waited behind a running push");
      began = Clock::now();
      heard->cancel();
      check(pushing.get() && ms(began) < 300, "a cancel of hearing waited behind the running push");
      slow = false;
      {
        auto long_finish = channel.recognizer();
        long_finish->begin();
        slow = true; fake_stages = 10;
        auto began_long = Clock::now();
        bool thrown = false;
        try { long_finish->finish_with_progress([] { throw std::runtime_error("watchdog passed"); }); }
        catch (const std::runtime_error& e) { thrown = std::string(e.what()) == "watchdog passed"; }
        check(thrown, "a progress callback's exception did not reach the caller");
        long_finish->reset();
        check(ms(began_long) < 400, "the server went on computing after a progress callback threw");
        slow = false; fake_stages = 3;
      }
      auto end = channel.endpoint();
      FakeEndpoint local_end;
      check(end->score(7, block) == local_end.score(7, block), "an endpoint score differs");
    }
    {
      const std::vector<uint8_t> recording(64000, 3);
      FakeEmbedder local;
      auto embedder = channel.speaker();
      check(embedder->embed(recording, 192) == local.embed(recording, 192), "a remote embedding differs from the local one");
      auto other = channel.speaker();
      aii::uid::Vector together_a, together_b;
      const std::vector<uint8_t> second(48000, 5);
      std::thread ta([&] { for (int i = 0; i < 50; ++i) together_a = embedder->embed(recording, 192); });
      std::thread tb([&] { for (int i = 0; i < 50; ++i) together_b = other->embed(second, 256); });
      ta.join(); tb.join();
      check(together_a == local.embed(recording, 192) && together_b == local.embed(second, 256), "two speakers at once differ from each alone");
      slow = true;
      auto embedding = std::async(std::launch::async, [&] {
        try { embedder->embed(recording, 192); return false; } catch (const Cancelled&) { return true; }
      });
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
      auto began = Clock::now();
      embedder->cancel();
      check(embedding.get() && ms(began) < 300, "a speaker's cancel waited behind its running embedding");
      slow = false;
      bool fenced = false;
      try { embedder->embed(recording, 192); } catch (const Cancelled&) { fenced = true; }
      check(fenced, "an embedding after a cancel ran before resume()");
      embedder->resume();
      check(embedder->embed(recording, 192) == local.embed(recording, 192), "resume() did not end the fence");
      slow = true;
      embedding = std::async(std::launch::async, [&] {
        try { embedder->embed(recording, 192); return false; } catch (const Cancelled&) { return true; }
      });
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
      began = Clock::now();
      auto resumed = std::async(std::launch::async, [&] { embedder->resume(); return ms(began); });
      std::this_thread::sleep_for(std::chrono::milliseconds(50));
      embedder->cancel();
      check(embedding.get(), "the running embedding was not cancelled");
      check(resumed.get() < 50, "a speaker's resume waited behind its running embedding");
      slow = false;
      fenced = false;
      try { embedder->embed(recording, 192); } catch (const Cancelled&) { fenced = true; }
      check(fenced, "a resume sent before a cancel lifted the cancel's fence");
      embedder->resume();
    }
    a.reset(); b.reset(); remote.reset();
    std::vector<std::unique_ptr<Synthesizer>> open;
    for (int i = 0; i < 16; ++i) open.push_back(channel.speech());
    bool refused = false;
    try { channel.speech(); } catch (const std::runtime_error& e) { refused = std::string(e.what()).find("at most 16") != std::string::npos; }
    check(refused, "a 17th stream was not refused");
    const int before = destroyed;
    open.pop_back();
    check(destroyed == before + 1, "a closed stream was not freed at its close");
    open.clear();
    for (int i = 0; i < 200; ++i) { auto s = channel.speech(); speak(*s, 1, "x"); }
    channel.speech().reset();
    check(model_channel_unjoined_threads() <= 2, "closed streams' threads were not joined");
    auto waiting = channel.speech();
    slow = true;
    waiting->open(); waiting->configure(settings); waiting->start(21, "never answered");
    auto lost = std::async(std::launch::async, [&] {
      try { waiting->next(); return std::string("answered"); } catch (const std::exception& e) { return std::string(e.what()); }
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    lose(ends.second);
    const auto status = lost.wait_for(std::chrono::seconds(1));
    check(status == std::future_status::ready && lost.get() == "model server lost", "a lost server left a call waiting");
    bool later = false;
    try { channel.speech(); } catch (const std::runtime_error& e) { later = std::string(e.what()) == "model server lost"; }
    check(later, "a call after the server was lost did not fail");
    slow = false;
    waiting.reset();
  }
  server.join();
  {
    const auto deaf_ends = model_channel_pipes();
    std::atomic<bool> said{false};
    Clock::time_point said_at{}, ended_at{};
    std::thread deaf_server([&] {
      serve_model_channel(factory, deaf_ends.second, std::chrono::milliseconds(200), [&](const std::string&) {
        said_at = Clock::now(); said = true;
      });
      close_channel_end(deaf_ends.second);
    });
    {
      ModelChannel channel(deaf_ends.first);
      auto s = channel.speech();
      SpeechSettings settings; s->open(); s->configure(settings); s->start(31, "deaf");
      deaf = true;
      auto stuck = std::async(std::launch::async, [&] {
        try { s->next(); return std::string("answered"); } catch (const std::exception& e) { return std::string(e.what()); }
      });
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
      ended_at = Clock::now();
      lose(deaf_ends.first);
      check(stuck.get() == "model server lost", "a call over an ended channel did not fail");
    }
    deaf_server.join();
    const double channel_end_ms = ms(ended_at);
    check(said, "a stream deaf to its cancel was never said to be hung");
    check(said && std::chrono::duration<double, std::milli>(said_at - ended_at).count() < 600,
          "a hung stream was said later than its retire");
    check(channel_end_ms < 900, "a channel's end waited for a stream deaf to its cancel");
    std::this_thread::sleep_for(std::chrono::milliseconds(1600));
    deaf = false;
  }
  if (!failed) std::puts("model channel: speech, hearing and the speaker cross whole and in order; cancel, check and resume never wait; a lost server fails its calls; streams bounded and freed; a stream deaf to cancel is bounded by retire");
  return failed;
}
