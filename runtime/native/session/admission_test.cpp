#include "admission.h"
#include "model_channel.h"
#include "model_link.h"
#include <csignal>
#include <cstdio>
#include <thread>
using namespace aii::voice;
using namespace std::chrono_literals;
namespace {
int failed = 0;
void check(bool ok, const char* why) { if (!ok) { std::fprintf(stderr, "FAILED: %s\n", why); ++failed; } }
LaneCosts costs{0.128, 380, true};
struct Factory final : StreamFactory {
  Admission gate{8, 60000ms, [] { return costs; }};
  std::unique_ptr<Synthesizer> speech() override { throw std::logic_error("no speech"); }
  std::unique_ptr<Recognizer> recognizer() override { throw std::logic_error("no hearing"); }
  std::unique_ptr<Endpoint> endpoint() override { throw std::logic_error("no endpoint"); }
  Admission* admission() override { return &gate; }
};
}

int main() {
#ifdef SIGPIPE
  std::signal(SIGPIPE, SIG_IGN);
#endif
  {
    Admission gate(8, 60000ms, [] { return costs; });
    for (uint64_t h = 1; h <= 5; ++h) check(gate.admit(h).admitted, "a session within the criterion was refused");
    check(gate.admit(3).admitted && gate.held() == 5, "asking again while holding changed what is held");
    const auto refused = gate.admit(6);
    check(!refused.admitted && refused.held == 5, "a session past the criterion was admitted");
    check(refused.retry_after_ms == 60000, "before any session was given back, the retry-after is not session_open");
    gate.release(2);
    check(gate.admit(6).admitted, "a slot given back was not free");
    gate.release(99);
    check(gate.held() == 5, "giving back a slot never held changed what is held");
    std::this_thread::sleep_for(50ms);
    gate.release(1);
    const auto later = gate.admit(7);
    check(later.admitted, "a slot given back was not free");
    const auto again = gate.admit(8);
    check(!again.admitted && again.retry_after_ms >= 1 && again.retry_after_ms < 60000,
          "after sessions were given back, the retry-after is not from their mean");
    Admission ceiling(3, 60000ms, [] { return LaneCosts{0.01, 10, true}; });
    for (uint64_t h = 1; h <= 3; ++h) check(ceiling.admit(h).admitted, "a session under the ceiling was refused");
    check(!ceiling.admit(4).admitted, "a session past the ceiling was admitted");
    Admission slow(5, 60000ms, [] { return LaneCosts{1.17, 3560, true}; });
    check(slow.admit(1).admitted, "a machine slower than the criterion alone refused its first session");
    check(!slow.admit(2).admitted, "a machine slower than the criterion alone admitted a second session");
    slow.release(1);
    check(slow.admit(2).admitted, "the first session's slot, given back, did not admit the next first");
    Admission rtf(16, 60000ms, [] { return LaneCosts{0.08, 150, true}; });
    size_t admitted = 0;
    for (uint64_t h = 1; h <= 16; ++h) admitted += rtf.admit(h).admitted;
    check(admitted == 10, "the real-time factor did not bound the sessions admitted");
    Admission mac(16, 60000ms, [] { return LaneCosts{0.128, 395, true}; });
    size_t mac_admitted = 0;
    for (uint64_t h = 1; h <= 16; ++h) mac_admitted += mac.admit(h).admitted;
    check(mac_admitted == 5, "W3's measured costs did not give W3's bound");
  }
  {
    Factory factory;
    costs = LaneCosts{0.3, 500, true};
    const auto a = model_channel_pipes(), b = model_channel_pipes(), c = model_channel_pipes();
    std::thread sa([&] { serve_model_channel(factory, a.second); close_channel_end(a.second); });
    std::thread sb([&] { serve_model_channel(factory, b.second); close_channel_end(b.second); });
    std::thread sc([&] { serve_model_channel(factory, c.second); close_channel_end(c.second); });
    {
      ModelChannel ca(a.first), cc(c.first);
      {
        ModelChannel cb(b.first);
        check(ca.admit().admitted && cb.admit().admitted, "two sessions within the criterion were refused over the channel");
        check(ca.admit().admitted && factory.gate.held() == 2, "asking again over the channel changed what is held");
        const auto third = cc.admit();
        check(!third.admitted && third.held == 2 && third.retry_after_ms > 0, "a third session was admitted over the channel");
      }
      sb.join();
      check(factory.gate.held() == 1, "a channel that ended kept its slot");
      check(cc.admit().admitted, "the slot of a channel that ended was not free");
      ca.release();
      check(factory.gate.held() == 1, "a slot given back over the channel was kept");
    }
    sa.join(); sc.join();
    check(factory.gate.held() == 0, "slots outlived their channels");
  }
  if (!failed) std::puts("admission: exactly as many sessions as the measured costs keep within the criterion, and the ceiling; slots given back and ended channels free; retry-after bounded");
  return failed;
}
