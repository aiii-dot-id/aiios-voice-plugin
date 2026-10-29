#include "model_server_control.h"
#include "model_link.h"
#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <thread>
#include <vector>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>
using namespace aii::voice;
namespace {
int failed = 0;
void check(bool ok, const char* why) { if (!ok) { std::fprintf(stderr, "FAILED: %s\n", why); ++failed; } }
std::atomic<int> made{0}, destroyed{0};
struct FakeSpeech final : Synthesizer {
  std::string text; int chunk = 0;
  FakeSpeech() { ++made; }
  ~FakeSpeech() override { ++destroyed; }
  void start(uint64_t, const std::string& t) override { text = t; chunk = 0; }
  std::vector<float> next() override {
    if (chunk++ == 2) return {};
    std::vector<float> pcm; for (unsigned char c : text) pcm.push_back(float(c) / 256.f + float(chunk)); return pcm;
  }
  void reset() override {}
  void cancel(uint64_t) noexcept override {}
};
struct Factory final : StreamFactory {
  std::unique_ptr<Synthesizer> speech() override { return std::make_unique<FakeSpeech>(); }
  std::unique_ptr<Recognizer> recognizer() override { throw std::logic_error("speech only"); }
  std::unique_ptr<Endpoint> endpoint() override { throw std::logic_error("speech only"); }
  std::string describe() override { return R"({"accelerator":"fake","models_loaded":4})"; }
};
std::vector<float> speak(Synthesizer& s, const std::string& text) {
  s.open(); s.start(1, text);
  std::vector<float> all;
  for (;;) { auto part = s.next(); if (part.empty()) break; all.insert(all.end(), part.begin(), part.end()); }
  s.reset(); return all;
}
bool peer_gone(int fd, int ms = 1000) {
  pollfd p{fd, POLLIN, 0};
  if (::poll(&p, 1, ms) <= 0) return false;
  char byte; return ::read(fd, &byte, 1) == 0;
}
char send_many(int control, const std::vector<int>& fds) {
  char byte = 'c'; iovec io{&byte, 1};
  std::vector<char> space(CMSG_SPACE(sizeof(int) * fds.size()));
  msghdr m{}; m.msg_iov = &io; m.msg_iovlen = 1; m.msg_control = space.data(); m.msg_controllen = space.size();
  auto* c = CMSG_FIRSTHDR(&m);
  c->cmsg_level = SOL_SOCKET; c->cmsg_type = SCM_RIGHTS; c->cmsg_len = CMSG_LEN(sizeof(int) * fds.size());
  std::memcpy(CMSG_DATA(c), fds.data(), sizeof(int) * fds.size());
  (void)::sendmsg(control, &m, 0);
  char word = 0;
  if (::read(control, &word, 1) != 1) std::fputs("the control socket ended\n", stderr);
  return word;
}
struct Pair { ChannelEnd mine, theirs; };
Pair pair() { const auto p = model_channel_pipes(); return {p.first, p.second}; }
bool hand(int control, const Pair& p) { const bool served = send_model_channel(control, p.theirs); close_channel_end(p.theirs); return served; }
}

int main() {
  std::signal(SIGPIPE, SIG_IGN);
  Factory factory;
  int control[2];
  if (socketpair(AF_UNIX, SOCK_STREAM, 0, control)) return 99;
  std::thread server([&] { serve_model_server(factory, control[1]); });
  const auto alone = [] { FakeSpeech direct; return speak(direct, "first voice"); }();
  {
    auto a = pair(), b = pair();
    check(hand(control[0], a), "a channel was not served");
    check(hand(control[0], b), "a channel was not served");
    ModelChannel ca(a.mine), cb(b.mine);
    check(ca.describe() == factory.describe(), "the server's description did not cross");
    auto sa = ca.speech(), sb = cb.speech();
    std::vector<float> together_a, together_b;
    std::thread ta([&] { for (int i = 0; i < 20; ++i) together_a = speak(*sa, "first voice"); });
    std::thread tb([&] { for (int i = 0; i < 20; ++i) together_b = speak(*sb, "second voice"); });
    ta.join(); tb.join();
    check(together_a == alone && !together_b.empty() && together_b != alone, "two channels at once differ from each alone");
    {
      auto swapped = pair();
      check(send_many(control[0], {int(swapped.theirs.out), int(swapped.theirs.in)}) == 'n', "swapped ends were not refused");
      close_channel_end(swapped.theirs);
      check(peer_gone(int(swapped.mine.in)), "refused ends were kept");
      close_channel_end(swapped.mine);
      int sockets[2]; if (socketpair(AF_UNIX, SOCK_STREAM, 0, sockets)) return 99;
      check(send_many(control[0], {sockets[0], sockets[1]}) == 'n', "a socket was served as a channel");
      ::close(sockets[0]); ::close(sockets[1]);
      auto alone_end = pair();
      check(send_many(control[0], {int(alone_end.theirs.in)}) == 'n', "one descriptor alone was served as a channel");
      close_channel_end(alone_end.theirs); close_channel_end(alone_end.mine);
    }
    auto first = pair(), extra = pair();
    check(send_many(control[0], {int(first.theirs.in), int(first.theirs.out), int(extra.theirs.in), int(extra.theirs.out)}) == 'y',
          "a channel sent with extra descriptors was not served");
    close_channel_end(first.theirs); close_channel_end(extra.theirs);
    check(peer_gone(int(extra.mine.in)), "a message's extra descriptors were kept");
    {
      ModelChannel cf(first.mine);
      auto s = cf.speech();
      check(speak(*s, "first voice") == alone, "a message's first two descriptors were not served");
    }
    close_channel_end(extra.mine);
    std::vector<Pair> held;
    for (size_t i = 2; i < model_server_channel_limit; ++i) {
      held.push_back(pair()); hand(control[0], held.back());
    }
    auto over = pair();
    check(!hand(control[0], over), "a channel past the limit was not refused");
    check(peer_gone(int(over.mine.in)), "a channel past the limit was served");
    close_channel_end(over.mine);
    for (auto& h : held) close_channel_end(h.mine);
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    auto again = pair();
    hand(control[0], again);
    auto later = pair();
    hand(control[0], later);
    {
      ModelChannel cl(later.mine);
      check(cl.describe() == factory.describe(), "channels that ended were not reaped");
    }
    close_channel_end(again.mine);
    const int before_end = destroyed;
    ::close(control[0]);
    server.join();
    check(destroyed >= before_end + 2, "the live channels' streams were not freed when the carrier went");
    bool lost = false;
    try { speak(*sa, "after"); } catch (const std::runtime_error& e) { lost = std::string(e.what()) == "model server lost"; }
    check(lost, "a channel outlived its server");
  }
  ::close(control[1]);
  check(made == destroyed, "a stream was never freed");
  if (!failed) std::puts("model server: channels, a pipe each way, given over its control socket, and only those; extras and wrong ends closed; bounded; all freed when its carrier goes");
  return failed;
}
