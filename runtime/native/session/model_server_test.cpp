#include "model_server_control.h"
#include "model_link.h"
#include <atomic>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <thread>
#include <vector>
#include <string>
#include <fcntl.h>
#include <poll.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/wait.h>
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

std::uint64_t soft_now() { rlimit l{}; ::getrlimit(RLIMIT_NOFILE, &l); return l.rlim_cur == RLIM_INFINITY ? UINT64_MAX : l.rlim_cur; }
bool limit_to(std::uint64_t soft, std::uint64_t hard) {
  rlimit l{}; ::getrlimit(RLIMIT_NOFILE, &l);
  if (hard) l.rlim_max = rlim_t(hard);
  l.rlim_cur = rlim_t(soft);
  return ::setrlimit(RLIMIT_NOFILE, &l) == 0;
}
void in_child(const char* why, int (*body)()) {
  std::fflush(stdout); std::fflush(stderr);
  const pid_t pid = ::fork();
  if (pid == 0) { std::_Exit(body()); }
  if (pid < 0) { check(false, why); std::fprintf(stderr, "  the child could not be made: %s\n", std::strerror(errno)); return; }
  int status = -1;
  pid_t got;
  do got = ::waitpid(pid, &status, 0); while (got < 0 && errno == EINTR);
  check(got == pid && WIFEXITED(status) && WEXITSTATUS(status) == 0, why);
}
#define REQUIRE(x) do { if (!(x)) { std::fprintf(stderr, "  child: %s\n", #x); return 1; } } while (0)
const std::uint64_t channels_need = model_server_descriptors_per_channel * model_server_channel_ceiling + model_server_descriptors_per_message;
int raised_to_the_need() {
  REQUIRE(limit_to(32, 0));
  const auto b = reserve_model_server_descriptors(model_server_channel_ceiling);
  REQUIRE(b.raised && b.need == b.open + channels_need && soft_now() == b.need);
  return 0;
}
int never_lowered() {
  const std::uint64_t high = count_descriptors().open + channels_need + 500;
  REQUIRE(limit_to(high, 0));
  const auto b = reserve_model_server_descriptors(model_server_channel_ceiling);
  REQUIRE(!b.raised && soft_now() == high && b.soft == high);
  return 0;
}
int refused_below_the_hard_limit() {
  REQUIRE(limit_to(64, 64));
  try {
    (void)reserve_model_server_descriptors(model_server_channel_ceiling);
  } catch (const DescriptorShortfall& e) {
    const std::string what = e.what();
    REQUIRE(what.find("hard open-file limit is 64") != std::string::npos && what.find(" need ") != std::string::npos);
    if (what.find("model_server_channels") == std::string::npos) std::fprintf(stderr, "  child: the shortfall said: %s\n", what.c_str());
    REQUIRE(what.find("model_server_channels") != std::string::npos);
    REQUIRE(soft_now() == 64);
    return 0;
  }
  return 1;
}
int ready_never_called_on_a_shortfall() {
  REQUIRE(limit_to(64, 64));
  bool ready = false;
  try {
    reserve_then_ready(model_server_channel_ceiling, [&](const DescriptorBudget&) { ready = true; });
  } catch (const DescriptorShortfall&) {
    REQUIRE(!ready);
    return 0;
  }
  return 1;
}
int ready_after_the_reservation() {
  REQUIRE(limit_to(32, 0));
  int calls = 0;
  reserve_then_ready(model_server_channel_ceiling, [&](const DescriptorBudget& b) { ++calls; if (soft_now() < b.need) calls = 100; });
  REQUIRE(calls == 1);
  return 0;
}
int server_refuses_to_start() {
  REQUIRE(limit_to(64, 64));
  int control[2];
  REQUIRE(::socketpair(AF_UNIX, SOCK_STREAM, 0, control) == 0);
  Factory f;
  try { serve_model_server(f, control[1], model_server_channel_ceiling); } catch (const DescriptorShortfall&) { return 0; }
  return 1;
}
int the_count_is_bounded() {
  REQUIRE(descriptor_scan_limit(UINT64_MAX) == descriptor_scan_bound && descriptor_scan_limit(100) == 100);
  REQUIRE(limit_to(1024, 0));
  const auto listed = count_descriptors(true), scanned = count_descriptors(false);
  REQUIRE(listed.complete && scanned.complete && scanned.scanned == 1024 && listed.open == scanned.open);
  return 0;
}
int a_failed_listing_is_no_count() {
  REQUIRE(!count_from_listing({"0", "1", "2"}, -1, true).complete);
  const auto whole = count_from_listing({".", "..", "0", "1", "2", "5"}, 5, false);
  REQUIRE(whole.complete && whole.open == 3);
  return 0;
}
int an_incomplete_count_shows_headroom_or_refuses() {
  DescriptorCount capped;
  capped.complete = false; capped.scanned = descriptor_scan_bound;
  capped.free_below = 100; capped.open = capped.scanned - capped.free_below;
  bool refused = false;
  try { (void)budget_for(capped, 1u << 20, UINT64_MAX, model_server_channel_ceiling); } catch (const DescriptorShortfall&) { refused = true; }
  REQUIRE(refused);
  capped.open = 10; capped.free_below = capped.scanned - capped.open;
  const auto b = budget_for(capped, 1u << 20, UINT64_MAX, model_server_channel_ceiling);
  REQUIRE(b.need == capped.open + channels_need && b.soft == (1u << 20));
  return 0;
}
int an_unrun_child_check_counts_as_failed() {
  rlimit none{1, 1};
  if (::setrlimit(RLIMIT_NPROC, &none) != 0) return 0;
  const pid_t probe = ::fork();
  if (probe == 0) std::_Exit(0);
  if (probe > 0) { int st = 0; ::waitpid(probe, &st, 0); return 0; }
  const int before = failed;
  in_child("(the harness control's own child, made to fail)", [] { return 0; });
  REQUIRE(failed == before + 1);
  return 0;
}
int a_refused_channel_is_said() {
  int err[2];
  REQUIRE(::pipe(err) == 0);
  REQUIRE(::dup2(err[1], 2) == 2);
  ::close(err[1]);
  (void)::fcntl(err[0], F_SETFL, O_NONBLOCK);
  int control[2];
  REQUIRE(::socketpair(AF_UNIX, SOCK_STREAM, 0, control) == 0);
  Factory f;
  auto p = pair();
  std::thread server([&] { serve_model_server(f, control[1], model_server_channel_ceiling); });
  int sockets[2];
  REQUIRE(::socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0);
  REQUIRE(send_many(control[0], {sockets[0], sockets[1]}) == 'n');
  ::close(sockets[0]); ::close(sockets[1]);
  std::vector<int> taken;
  for (int fd; (fd = ::dup(err[0])) >= 0;) taken.push_back(fd);
  for (int i = 0; i < 3 && !taken.empty(); ++i) { ::close(taken.back()); taken.pop_back(); }
  const bool served = send_model_channel(control[0], p.theirs);
  for (int fd : taken) ::close(fd);
  ::close(control[0]);
  server.join();
  std::fflush(stderr);
  char buf[4096] = {};
  const ssize_t n = ::read(err[0], buf, sizeof buf - 1);
  const std::string said = n > 0 ? std::string(buf, size_t(n)) : std::string();
  REQUIRE(!served);
  REQUIRE(said.find("\"event\":\"channel_refused\"") != std::string::npos);
  return 0;
}
int four_channels_are_the_need_and_the_bound() {
  REQUIRE(limit_to(32, 0));
  const auto b = reserve_model_server_descriptors(4);
  REQUIRE(b.raised && b.need == b.open + model_server_descriptors_per_channel * 4 + model_server_descriptors_per_message &&
          soft_now() == b.need);
  REQUIRE(limit_to(b.soft + 2 * 5 + 16, 0));
  int control[2];
  REQUIRE(::socketpair(AF_UNIX, SOCK_STREAM, 0, control) == 0);
  Factory f;
  std::thread server([&] { serve_model_server(f, control[1], 4); });
  std::vector<Pair> held;
  int served = 0;
  for (int i = 0; i < 4; ++i) { held.push_back(pair()); served += hand(control[0], held.back()) ? 1 : 0; }
  auto fifth = pair();
  const bool fifth_served = hand(control[0], fifth);
  const bool fifth_closed = peer_gone(int(fifth.mine.in));
  ::close(control[0]);
  server.join();
  ::close(control[1]);
  close_channel_end(fifth.mine);
  for (auto& h : held) close_channel_end(h.mine);
  if (served != 4 || fifth_served) std::fprintf(stderr, "  child: of five channels given to a server of four, %d of the first four were served and the fifth %s\n",
                                                served, fifth_served ? "was served" : "was refused");
  REQUIRE(served == 4);
  REQUIRE(!fifth_served && fifth_closed);
  return 0;
}
void descriptor_contract() {
  in_child("a soft limit below the need was not raised to it exactly", raised_to_the_need);
  in_child("a soft limit above the need was lowered", never_lowered);
  in_child("a hard limit below the need was not refused, naming both, with soft untouched", refused_below_the_hard_limit);
  in_child("ready was called although the descriptors fell short", ready_never_called_on_a_shortfall);
  in_child("ready was not called once, after the reservation", ready_after_the_reservation);
  in_child("the server started below its need", server_refuses_to_start);
  in_child("the descriptor count's listing and bounded scan disagree, or the bound does not hold", the_count_is_bounded);
  in_child("a listing that failed part way was taken as a count", a_failed_listing_is_no_count);
  in_child("an incomplete count neither showed headroom nor refused", an_incomplete_count_shows_headroom_or_refuses);
  in_child("a child check that could not be made was not counted as failed", an_unrun_child_check_counts_as_failed);
  in_child("a channel whose Link could not be made was not refused and said", a_refused_channel_is_said);
  in_child("four channels were not the need, or a server given four did not serve four and refuse the fifth",
           four_channels_are_the_need_and_the_bound);
}
}

int main() {
  std::signal(SIGPIPE, SIG_IGN);
  descriptor_contract();
  {
    const auto b = reserve_model_server_descriptors(model_server_channel_ceiling);
    const std::uint64_t own = 2 * model_server_channel_ceiling + 4 * 4 + 16;
    if (soft_now() < b.need + own && !limit_to(b.soft + own, 0)) return 98;
  }
  Factory factory;
  int control[2];
  if (socketpair(AF_UNIX, SOCK_STREAM, 0, control)) return 99;
  std::thread server([&] { serve_model_server(factory, control[1], model_server_channel_ceiling); });
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
    for (size_t i = 2; i < model_server_channel_ceiling; ++i) {
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
