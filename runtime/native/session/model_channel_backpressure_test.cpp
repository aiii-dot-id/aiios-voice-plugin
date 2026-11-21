#include "model_channel.h"
#include "model_link.h"
#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <future>
#include <thread>
#include <fcntl.h>
#include <poll.h>
#include <unistd.h>
using namespace aii::voice;
using Clock = std::chrono::steady_clock;
namespace {
int failed = 0;
void check(bool ok, const char* why) { if (!ok) { std::fprintf(stderr, "FAILED: %s\n", why); ++failed; } }
double ms(Clock::time_point since) { return std::chrono::duration<double, std::milli>(Clock::now() - since).count(); }
std::atomic<int64_t> cancelled_seen_ns{0};
std::string big_info;
struct FakeSpeech final : Synthesizer {
  std::atomic<bool> cancelled{false}; uint64_t id = 0;
  void start(uint64_t generation, const std::string&) override { id = generation; cancelled = false; }
  std::vector<float> next() override {
    const auto begun = Clock::now();
    while (!cancelled && ms(begun) < 3000) std::this_thread::sleep_for(std::chrono::milliseconds(2));
    if (cancelled) {
      cancelled_seen_ns = Clock::now().time_since_epoch().count();
      throw Cancelled("synthesis cancelled during inference");
    }
    return {};
  }
  size_t playback_reserve_samples() const override { return 4800; }
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
};
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
bool send_within(int fd, const std::string& raw, std::chrono::milliseconds bound) {
  const int flags = ::fcntl(fd, F_GETFL);
  (void)::fcntl(fd, F_SETFL, flags | O_NONBLOCK);
  size_t at = 0; const auto until = Clock::now() + bound; bool ok = true;
  while (at < raw.size()) {
    const auto n = ::write(fd, raw.data() + at, raw.size() - at);
    if (n > 0) { at += size_t(n); continue; }
    if ((n < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) || Clock::now() > until) { ok = false; break; }
    pollfd p{fd, POLLOUT, 0}; (void)::poll(&p, 1, 10);
  }
  (void)::fcntl(fd, F_SETFL, flags);
  return ok;
}
bool read_exact(int fd, void* to, size_t n) {
  auto* p = static_cast<char*>(to);
  while (n) { const auto got = ::read(fd, p, n); if (got <= 0) return false; p += got; n -= size_t(got); }
  return true;
}
bool answer(int fd, uint64_t& stream, uint8_t& status) {
  uint32_t length = 0;
  if (!read_exact(fd, &length, 4) || length < 18) return false;
  std::string rest(length, '\0');
  if (!read_exact(fd, rest.data(), length)) return false;
  std::memcpy(&stream, rest.data() + 9, 8); status = uint8_t(rest[17]);
  return true;
}
}

int main(int argc, char** argv) {
  std::signal(SIGPIPE, SIG_IGN);
  Factory factory;
  const auto runs = [&](const char* name) { return argc < 2 || std::strcmp(argv[1], name) == 0; };
  if (runs("client")) {
    const auto pipes1 = model_channel_pipes();
    const ChannelEnd client = pipes1.first, server = pipes1.second;
    const int from_client = int(server.in), to_client = int(server.out);
    std::thread deaf_server([&] {
      uint32_t length = 0; std::string rest;
      if (read_exact(from_client, &length, 4)) { rest.resize(length); (void)read_exact(from_client, rest.data(), length); }
      uint64_t request = 0; std::memcpy(&request, rest.data() + 1, 8);
      const auto raw = frame(Result, request, 1, std::string(1, '\0'));
      (void)!::write(to_client, raw.data(), raw.size());
    });
    {
      ModelChannel channel(client);
      auto s = channel.speech();
      deaf_server.join();
      auto call = std::async(std::launch::async, [&] {
        try { s->start(1, std::string(8u << 20, 'x')); return std::string("answered"); }
        catch (const std::exception& x) { return std::string(x.what()); }
      });
      std::this_thread::sleep_for(std::chrono::milliseconds(200));
      const auto began = Clock::now();
      auto cancelling = std::async(std::launch::async, [&] { s->cancel(1); });
      const bool returned = cancelling.wait_for(std::chrono::milliseconds(500)) == std::future_status::ready;
      check(returned && ms(began) < 50, "a cancel waited behind a call's write the server was not reading");
      close_channel_end(server);
      check(call.get() == "model server lost", "a call to a server that went did not fail");
      cancelling.wait();
    }
  }
  if (runs("server")) {
    const auto pipes2 = model_channel_pipes();
    const ChannelEnd client = pipes2.first, served = pipes2.second;
    const int to_server = int(client.out), from_server = int(client.in);
    std::thread server([&] { serve_model_channel(factory, served); close_channel_end(served); });
    uint64_t stream = 0; uint8_t status = 0;
    bool ok = send_within(to_server, frame(Open, 1, 0, std::string(1, char(1))), std::chrono::milliseconds(1000)) &&
              answer(from_server, stream, status) && status == 0;
    ok = ok && send_within(to_server, frame(Call, 2, stream, std::string(1, char(5)) + put<uint64_t>(7) + put<uint32_t>(1) + "x"),
                           std::chrono::milliseconds(1000)) && answer(from_server, stream, status) && status == 0;
    ok = ok && send_within(to_server, frame(Call, 3, stream, std::string(1, char(6))), std::chrono::milliseconds(1000));
    check(ok, "the stream could not be opened and started");
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    bool flooded = true;
    for (uint64_t r = 4; r < 20004 && flooded; ++r)
      flooded = send_within(to_server, frame(Call, r, stream, std::string(1, char(2))), std::chrono::milliseconds(1000));
    check(flooded, "the server stopped reading while its answers were not being read");
    cancelled_seen_ns = 0;
    const auto cancel_sent = Clock::now();
    const bool sent = send_within(to_server, frame(Cancel, 0, stream, put<uint64_t>(7)), std::chrono::milliseconds(1000));
    while (!cancelled_seen_ns && ms(cancel_sent) < 2000) std::this_thread::sleep_for(std::chrono::milliseconds(2));
    const double applied_ms = cancelled_seen_ns
        ? std::chrono::duration<double, std::milli>(Clock::time_point(Clock::duration(cancelled_seen_ns.load())) - cancel_sent).count()
        : 1e9;
    check(sent && applied_ms < 300, "a cancel was not applied while the server's answers went unread");
    close_channel_end(client);
    server.join();
  }
  if (runs("bound")) {
    big_info.assign(1u << 20, 'i');
    const auto pipes3 = model_channel_pipes();
    const ChannelEnd client = pipes3.first, flooded = pipes3.second;
    const auto pipes4 = model_channel_pipes();
    const ChannelEnd other_client = pipes4.first, other = pipes4.second;
    const int to_server = int(client.out), from_server = int(client.in);
    std::atomic<bool> ended{false};
    std::thread flooded_server([&] { serve_model_channel(factory, flooded); ended = true; close_channel_end(flooded); });
    std::thread other_server([&] { serve_model_channel(factory, other); close_channel_end(other); });
    uint64_t stream = 0; uint8_t status = 0;
    bool ok = send_within(to_server, frame(Open, 1, 0, std::string(1, char(2))), std::chrono::milliseconds(1000));
    ok = ok && answer(from_server, stream, status) && status == 0;
    check(ok, "the hearing stream could not be opened");
    const auto began = Clock::now();
    for (uint64_t r = 2; r < 202 && !ended; ++r)
      if (!send_within(to_server, frame(Call, r, stream, std::string(1, char(12))), std::chrono::milliseconds(1000))) break;
    while (!ended && ms(began) < 5000) std::this_thread::sleep_for(std::chrono::milliseconds(5));
    check(ended, "a channel whose answers were never read was not ended at the bound");
    {
      ModelChannel other_channel(other_client);
      auto s = other_channel.recognizer();
      big_info = "{}";
      check(s->execution_info() == "{}", "another channel was ended with the one that did not read");
    }
    close_channel_end(client);
    flooded_server.join(); other_server.join();
  }
  if (!failed) std::puts("model channel under backpressure: a cancel never waits behind a write; a server not read still applies cancels; a peer that never reads ends only its own channel");
  return failed;
}
