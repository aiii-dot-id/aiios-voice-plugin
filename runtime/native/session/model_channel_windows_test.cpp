#include "model_channel.h"
#include "model_link.h"
#include "model_server_control.h"
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <future>
#include <thread>
using namespace aii::voice;
using Clock = std::chrono::steady_clock;
using namespace std::chrono_literals;
namespace {
int failed = 0;
void check(bool ok, const char* why) { if (!ok) { std::fprintf(stderr, "FAILED: %s\n", why); std::fflush(stderr); ++failed; } }
void section(const char* name) { std::printf("%s\n", name); std::fflush(stdout); }
double ms(Clock::time_point since) { return std::chrono::duration<double, std::milli>(Clock::now() - since).count(); }
std::atomic<bool> slow{false}, deaf{false};
std::atomic<int64_t> cancelled_seen_ns{0};
std::atomic<int> made{0}, destroyed{0};
std::string big_info = "{\"encoder_provider\":\"fake\"}";
struct FakeSpeech final : Synthesizer {
  std::string text; uint64_t id = 0; int chunk = 0; std::atomic<bool> cancelled{false};
  FakeSpeech() { ++made; }
  ~FakeSpeech() override { ++destroyed; }
  void check(const SpeechSettings& s) const override { if (s.voice == "bad") throw std::invalid_argument("no such voice"); }
  size_t playback_reserve_samples() const override { return 4800; }
  void start(uint64_t generation, const std::string& t) override { id = generation; text = t; chunk = 0; cancelled = false; }
  std::vector<float> next() override {
    if (deaf) std::this_thread::sleep_for(1500ms);
    const auto begun = Clock::now();
    while (slow && !cancelled && ms(begun) < 3000) std::this_thread::sleep_for(2ms);
    if (cancelled) { cancelled_seen_ns = Clock::now().time_since_epoch().count(); throw Cancelled("synthesis cancelled during inference"); }
    if (chunk == 3) return {};
    std::vector<float> pcm;
    for (unsigned char c : text) pcm.push_back(float(c) / 256.f + float(chunk) + float(id) / 1000.f);
    ++chunk;
    return pcm;
  }
  void reset() override {}
  void cancel(uint64_t generation) noexcept override { if (generation == id) cancelled = true; }
};
struct FakeHearing final : Recognizer {
  std::string execution_info() const override { return big_info; }
  void begin() override {}
  std::string push(const float*, size_t) override { return {}; }
  std::string finish() override { return {}; }
  bool separated() const override { return false; }
  bool continuous_input() const override { return false; }
  void reset() override {}
  void cancel() noexcept override {}
};
struct Factory final : StreamFactory {
  std::unique_ptr<Synthesizer> speech() override { return std::make_unique<FakeSpeech>(); }
  std::unique_ptr<Recognizer> recognizer() override { return std::make_unique<FakeHearing>(); }
  std::unique_ptr<Endpoint> endpoint() override { throw std::logic_error("no endpoint"); }
  std::string describe() override { return "fake\n{}"; }
};
std::vector<float> speak(Synthesizer& s, uint64_t id, const std::string& text) {
  SpeechSettings settings; s.open(); s.configure(settings); s.start(id, text);
  std::vector<float> all;
  for (;;) { auto part = s.next(); if (part.empty()) break; all.insert(all.end(), part.begin(), part.end()); }
  s.reset(); return all;
}
struct Pipes { ChannelEnd a, b; };
Pipes pipes(DWORD size = 0) {
  HANDLE r1, w1, r2, w2;
  if (!CreatePipe(&r1, &w1, nullptr, size) || !CreatePipe(&r2, &w2, nullptr, size)) std::abort();
  return {{intptr_t(r2), intptr_t(w1)}, {intptr_t(r1), intptr_t(w2)}};
}
void close_end(ChannelEnd e) { CloseHandle(HANDLE(e.in)); CloseHandle(HANDLE(e.out)); }
enum : uint8_t { Open = 1, Call = 2, Cancel = 3, Result = 5 };
std::string frame(uint8_t kind, uint64_t request, uint64_t stream, const std::string& payload) {
  const uint32_t length = uint32_t(17 + payload.size());
  std::string raw(4 + length, '\0');
  std::memcpy(raw.data(), &length, 4); raw[4] = char(kind);
  std::memcpy(raw.data() + 5, &request, 8); std::memcpy(raw.data() + 13, &stream, 8);
  std::memcpy(raw.data() + 21, payload.data(), payload.size());
  return raw;
}
template <class T> std::string put(T v) { return std::string(reinterpret_cast<const char*>(&v), sizeof v); }
bool write_raw(intptr_t h, const std::string& raw) {
  DWORD n = 0; return WriteFile(HANDLE(h), raw.data(), DWORD(raw.size()), &n, nullptr) && n == raw.size();
}
bool read_raw(intptr_t h, void* to, DWORD n) {
  auto* p = static_cast<char*>(to);
  while (n) { DWORD got = 0; if (!ReadFile(HANDLE(h), p, n, &got, nullptr) || !got) return false; p += got; n -= got; }
  return true;
}
bool answer(intptr_t h, uint64_t& stream, uint8_t& status) {
  uint32_t length = 0;
  if (!read_raw(h, &length, 4) || length < 18) return false;
  std::string rest(length, '\0');
  if (!read_raw(h, rest.data(), length)) return false;
  std::memcpy(&stream, rest.data() + 9, 8); status = uint8_t(rest[17]);
  return true;
}
}

int main() {
  SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
  Factory factory;
  section("1");
  {
    auto p = pipes();
    Link server_link(p.b);
    std::thread server([&] { serve_model_channel(factory, server_link, std::chrono::milliseconds::zero(), {}); });
    std::vector<float> alone_a, alone_b;
    { FakeSpeech d; alone_a = speak(d, 7, "first voice"); }
    { FakeSpeech d; alone_b = speak(d, 9, "second voice, a little longer"); }
    {
      ModelChannel channel(p.a);
      check(channel.describe() == factory.describe(), "the server's description did not cross");
      auto a = channel.speech(), b = channel.speech();
      std::vector<float> together_a, together_b;
      std::thread ta([&] { for (int i = 0; i < 30; ++i) together_a = speak(*a, 7, "first voice"); });
      std::thread tb([&] { for (int i = 0; i < 30; ++i) together_b = speak(*b, 9, "second voice, a little longer"); });
      ta.join(); tb.join();
      check(together_a == alone_a && together_b == alone_b, "two streams at once differ from each alone");
      slow = true;
      SpeechSettings settings; a->open(); a->configure(settings); a->start(11, "held");
      auto held = std::async(std::launch::async, [&] { try { a->next(); return false; } catch (const Cancelled&) { return true; } });
      std::this_thread::sleep_for(100ms);
      auto began = Clock::now(); a->check(settings);
      check(ms(began) < 100, "check() waited behind a running next()");
      began = Clock::now(); a->cancel(11);
      check(held.get() && ms(began) < 300, "a cancel waited behind the running call");
      a->reset();
      b->open(); b->configure(settings); b->start(21, "never answered");
      auto lost = std::async(std::launch::async, [&] {
        try { b->next(); return std::string("answered"); } catch (const std::exception& e) { return std::string(e.what()); }
      });
      std::this_thread::sleep_for(50ms);
      server_link.end(); server.join(); close_end(p.b);
      check(lost.wait_for(2s) == std::future_status::ready && lost.get() == "model server lost", "a server that went left a call waiting");
      check(channel.lost(), "the channel did not say its server was lost");
      slow = false;
    }
  }
  section("2");
  {
    auto p = pipes(4096);
    std::thread handshake([&] {
      uint32_t length = 0; std::string rest;
      if (read_raw(p.b.in, &length, 4)) { rest.resize(length); (void)read_raw(p.b.in, rest.data(), length); }
      uint64_t request = 0; std::memcpy(&request, rest.data() + 1, 8);
      (void)write_raw(p.b.out, frame(Result, request, 1, std::string(1, '\0')));
    });
    {
      ModelChannel channel(p.a);
      auto s = channel.speech();
      handshake.join();
      auto call = std::async(std::launch::async, [&] {
        try { s->start(1, std::string(8u << 20, 'x')); return std::string("answered"); }
        catch (const std::exception& x) { return std::string(x.what()); }
      });
      std::this_thread::sleep_for(200ms);
      const auto began = Clock::now();
      auto cancelling = std::async(std::launch::async, [&] { s->cancel(1); });
      const bool returned = cancelling.wait_for(500ms) == std::future_status::ready;
      check(returned && ms(began) < 50, "a cancel waited behind a call's write the server was not reading");
      close_end(p.b);
      check(call.wait_for(2s) == std::future_status::ready && call.get() == "model server lost", "a call to a server that went did not fail");
      cancelling.wait();
    }
  }
  section("3");
  {
    auto p = pipes(4096);
    Link server_link(p.b);
    std::thread server([&] { serve_model_channel(factory, server_link, std::chrono::milliseconds::zero(), {}); });
    uint64_t stream = 0; uint8_t status = 0;
    bool ok = write_raw(p.a.out, frame(Open, 1, 0, std::string(1, char(1)))) && answer(p.a.in, stream, status) && status == 0;
    ok = ok && write_raw(p.a.out, frame(Call, 2, stream, std::string(1, char(5)) + put<uint64_t>(7) + put<uint32_t>(1) + "x")) &&
         answer(p.a.in, stream, status) && status == 0;
    slow = true;
    ok = ok && write_raw(p.a.out, frame(Call, 3, stream, std::string(1, char(6))));
    check(ok, "the stream could not be opened and started");
    std::this_thread::sleep_for(50ms);
    cancelled_seen_ns = 0;
    auto flood = std::async(std::launch::async, [&] {
      for (uint64_t r = 4; r < 20004; ++r) if (!write_raw(p.a.out, frame(Call, r, stream, std::string(1, char(2))))) return false;
      return write_raw(p.a.out, frame(Cancel, 0, stream, put<uint64_t>(7)));
    });
    const bool sent = flood.wait_for(5s) == std::future_status::ready && flood.get();
    check(sent, "the server stopped reading while its answers were not being read");
    const auto cancel_sent = Clock::now();
    while (!cancelled_seen_ns && ms(cancel_sent) < 2000) std::this_thread::sleep_for(2ms);
    check(cancelled_seen_ns != 0, "a cancel was not applied while the server's answers went unread");
    slow = false;
    server_link.end(); server.join();
    close_end(p.a); close_end(p.b);
    if (flood.valid()) flood.wait();
  }
  section("4");
  {
    big_info.assign(1u << 20, 'i');
    auto p = pipes(4096), q = pipes();
    std::atomic<bool> ended{false};
    Link flooded_link(p.b), other_link(q.b);
    std::thread flooded([&] { serve_model_channel(factory, flooded_link, std::chrono::milliseconds::zero(), {}); ended = true; });
    std::thread other([&] { serve_model_channel(factory, other_link, std::chrono::milliseconds::zero(), {}); });
    uint64_t stream = 0; uint8_t status = 0;
    check(write_raw(p.a.out, frame(Open, 1, 0, std::string(1, char(2)))) && answer(p.a.in, stream, status) && status == 0,
          "the hearing stream could not be opened");
    section("4: opened");
    auto flood = std::async(std::launch::async, [&] {
      for (uint64_t r = 2; r < 202 && !ended; ++r) if (!write_raw(p.a.out, frame(Call, r, stream, std::string(1, char(12))))) return;
    });
    const auto began = Clock::now();
    while (!ended && ms(began) < 10000) std::this_thread::sleep_for(5ms);
    check(ended, "a channel whose answers were never read was not ended at the bound");
    section("4: bound judged");
    {
      ModelChannel channel(q.a);
      section("4: other: channel made");
      auto s = channel.recognizer();
      section("4: other: recognizer opened");
      big_info = "{}";
      check(s->execution_info() == "{}", "another channel was ended with the one that did not read");
      section("4: other: execution_info answered");
      other_link.end();
      section("4: other: link ended");
      other.join();
      close_end(q.b);
      section("4: other: server joined");
    }
    section("4: other channel judged");
    flooded.join();
    section("4: flooded server joined");
    close_end(p.b);
    flood.wait();
    section("4: flood ended");
    close_end(p.a);
  }
  section("5");
  {
    auto p = pipes();
    Link server_link(p.b);
    std::atomic<bool> said{false};
    Clock::time_point said_at{}, ended_at{};
    std::thread server([&] {
      serve_model_channel(factory, server_link, std::chrono::milliseconds(200), [&](const std::string&) { said_at = Clock::now(); said = true; });
    });
    uint64_t stream = 0; uint8_t status = 0;
    bool ok = write_raw(p.a.out, frame(Open, 1, 0, std::string(1, char(1)))) && answer(p.a.in, stream, status) &&
              write_raw(p.a.out, frame(Call, 2, stream, std::string(1, char(5)) + put<uint64_t>(31) + put<uint32_t>(1) + "d")) &&
              answer(p.a.in, stream, status);
    deaf = true;
    ok = ok && write_raw(p.a.out, frame(Call, 3, stream, std::string(1, char(6))));
    check(ok, "the deaf stream could not be started");
    std::this_thread::sleep_for(100ms);
    ended_at = Clock::now();
    close_end(p.a);
    server.join();
    check(said, "a stream deaf to its cancel was never said to be hung");
    check(said && std::chrono::duration<double, std::milli>(said_at - ended_at).count() < 600, "a hung stream was said later than its retire");
    check(ms(ended_at) < 900, "a channel's end waited for a stream deaf to its cancel");
    std::this_thread::sleep_for(1600ms);
    deaf = false;
    close_end(p.b);
  }
  section("6");
  {
    auto control = pipes();
    std::thread server([&] { serve_model_server(factory, control.b); });
    const int before = destroyed;
    auto c = pipes();
    check(send_model_channel(control.a, GetCurrentProcess(), c.b), "a channel was not served");
    close_end(c.b);
    {
      ModelChannel channel(c.a);
      check(channel.describe() == factory.describe(), "a channel given by handle values was not served");
      auto s = channel.speech();
      check(!speak(*s, 1, "x").empty(), "a channel given by handle values did not speak");
    }
    HANDLE event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    check(!send_model_channel(control.a, GetCurrentProcess(), ChannelEnd{intptr_t(event), intptr_t(event)}),
          "a handle that is not a pipe was not refused");
    const uint64_t own[2] = {uint64_t(control.b.in), uint64_t(control.b.out)};
    DWORD n = 0; char word = 0;
    check(WriteFile(HANDLE(control.a.out), own, sizeof own, &n, nullptr) && ReadFile(HANDLE(control.a.in), &word, 1, &n, nullptr) && word == 'n',
          "the server's own handles were not refused");
    std::vector<ChannelEnd> held;
    for (size_t i = 0; i < model_server_channel_limit; ++i) {
      auto h = pipes();
      check(send_model_channel(control.a, GetCurrentProcess(), h.b), "a channel under the limit was refused");
      close_end(h.b); held.push_back(h.a);
    }
    auto over = pipes();
    check(!send_model_channel(control.a, GetCurrentProcess(), over.b), "a channel past the limit was not refused");
    close_end(over.b); close_end(over.a);
    for (auto& h : held) close_end(h);
    auto last = pipes();
    std::this_thread::sleep_for(100ms);
    check(send_model_channel(control.a, GetCurrentProcess(), last.b), "channels that ended were not reaped");
    close_end(last.b);
    ModelChannel kept(last.a);
    auto s = kept.speech();
    close_end(control.a);
    server.join();
    close_end(control.b);
    check(destroyed > before, "the live channels' streams were not freed when the carrier went");
    bool lost = false;
    try { speak(*s, 1, "after"); } catch (const std::runtime_error& e) { lost = std::string(e.what()) == "model server lost"; }
    check(lost, "a channel outlived its server");
    CloseHandle(event);
  }
  if (!failed) std::puts("model channel on Windows: speech crosses whole; check and cancel never wait; a server gone fails its calls; "
                         "cancel never waits behind an unread write; an unread server still cancels; the bound ends only its channel; "
                         "a deaf stream is bounded; the control takes only pipes it was given");
  return failed;
}
