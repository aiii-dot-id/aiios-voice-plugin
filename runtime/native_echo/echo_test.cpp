#include "echo.h"
#include <array>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <limits>

void check(bool ok, const char *why) {
  if (!ok) {
    std::cerr << why << '\n';
    std::exit(1);
  }
}
int main() {
  aii_echo *e = nullptr;
  check(aii_echo_create(0, &e) == AII_ECHO_INVALID && !e, "zero generation");
  check(aii_echo_create(1, &e) == AII_ECHO_OK, "create");
  std::array<float, 160> near{}, far{}, out{};
  size_t emitted = 99;
  for (size_t i = 0; i < 160; ++i)
    near[i] = float(.1 * std::sin(i * .1));
  auto run = [&](uint64_t g, uint64_t t, unsigned f = 1) {
    return aii_echo_process(e, g, t, near.data(), far.data(), 160, f,
                            out.data(), &emitted);
  };
  check(run(1, 0) == 0 && emitted == 0, "first frame buffered");
  auto unchanged = out;
  check(run(2, 160) == AII_ECHO_INVALID && out == unchanged,
        "foreign generation");
  check(run(1, 0) == AII_ECHO_INVALID && out == unchanged, "duplicate clock");
  check(run(1, 320) == AII_ECHO_INVALID, "gap clock");
  check(run(1, 160, 2) == AII_ECHO_INVALID, "unknown flags");
  near[0] = std::numeric_limits<float>::quiet_NaN();
  check(run(1, 160) == AII_ECHO_INVALID, "NaN");
  near[0] = 0;
  far[0] = 2;
  check(run(1, 160) == AII_ECHO_INVALID, "invalid reference");
  far[0] = .2;
  check(aii_echo_process(e, 1, 160, near.data(), far.data(), 160, 1,
                         near.data(), &emitted) == AII_ECHO_INVALID,
        "alias");
  check(run(1, 160) == 0, "valid after rejected frames");
  check(emitted == 160 && out == near, "near-only exact passthrough");
  aii_echo_status s{};
  check(aii_echo_get_status(e, &s) == 0 && s.state == AII_ECHO_PROCESSING,
        "processing state");
  check(aii_echo_process(e, 1, 320, near.data(), nullptr, 160, 0, out.data(),
                         &emitted) == 0 &&
            out == near,
        "missing ref passthrough");
  check(aii_echo_get_status(e, &s) == 0 &&
            s.state == AII_ECHO_REFERENCE_MISSING &&
            s.missing_reference_frames == 1,
        "missing not silence");
  check(aii_echo_reset(e, 1) == AII_ECHO_INVALID, "old reset");
  check(aii_echo_reset(e, 2) == 0, "new generation");
  far.fill(0);
  check(run(1, 0) == AII_ECHO_INVALID, "old session cannot revive");
  check(run(2, 0) == 0 && emitted == 0, "reset removes tail");
  check(aii_echo_get_status(e, &s) == 0 && s.processed_frames == 1 &&
            s.next_sample == 160,
        "reset counters");
  check(aii_echo_finish(e, 2, out.data(), &emitted) == 0 && emitted == 160 &&
            out == near,
        "finish keeps last real frame");
  check(aii_echo_finish(e, 2, out.data(), &emitted) == 0 && emitted == 0,
        "finish once");
  check(run(2, 160) == AII_ECHO_INVALID, "input after finish");
  check(aii_echo_get_status(e, &s) == 0 && s.next_sample == 160 &&
            s.processed_frames == 1,
        "padding is not capture");
  // Real backend, deterministic broadband echo with 40 ms acoustic delay.
  check(aii_echo_reset(e, 3) == 0, "echo generation");
  std::array<float, 800> ring{};
  size_t pos = 0;
  uint32_t seed = 7;
  double input_energy = 0, output_energy = 0;
  for (uint64_t f = 0; f < 1800; ++f) {
    for (size_t i = 0; i < 160; ++i) {
      seed = 1664525u * seed + 1013904223u;
      far[i] = (float(seed >> 8) / 16777216.f - .5f) * .3f;
      ring[pos] = far[i];
      near[i] = .55f * ring[(pos + ring.size() - 640) % ring.size()];
      pos = (pos + 1) % ring.size();
    }
    check(run(3, f * 160) == 0, "real backend frame");
    for (size_t i = 0; i < 160; ++i) {
      check(std::isfinite(out[i]), "finite backend output");
      if (f >= 1000) {
        input_energy += near[i] * near[i];
        output_energy += out[i] * out[i];
      }
    }
  }
  double erle = 10 * std::log10(input_energy / std::max(output_energy, 1e-20));
  std::cout << "steady_echo_reduction_db=" << erle << '\n';
  check(erle > 15, "controlled delayed echo reduction below 15dB");
  // Exact ordering when the render tail expires: backend block delay must
  // never make the unprocessed path jump forward by 128 samples.
  check(aii_echo_reset(e, 4) == 0, "transition generation");
  std::array<float, 160> previous{};
  for (uint64_t f = 0; f < 180; ++f) {
    for (size_t i = 0; i < 160; ++i)
      near[i] = float(.1 * std::sin(double(f * 160 + i) * .07));
    far.fill(0);
    if (f == 0)
      far[0] = .1f;
    check(run(4, f * 160) == 0, "transition frame");
    if (f > 105)
      check(emitted == 160 && out == previous, "bypass sample timeline");
    previous = near;
  }
  check(aii_echo_finish(e, 4, out.data(), &emitted) == 0 && emitted == 160 &&
            out == previous,
        "transition final tail");
  for (uint64_t n = 1; n < 160; ++n) {
    const uint64_t g = 4 + n;
    check(aii_echo_reset(e, g) == 0, "short-tail reset");
    far.fill(0);
    check(aii_echo_process(e, g, 0, near.data(), far.data(), n, 1, out.data(),
                           &emitted) == 0 &&
              emitted == 0,
          "short-tail accept");
    check(aii_echo_process(e, g, n, near.data(), far.data(), 160, 1, out.data(),
                           &emitted) == AII_ECHO_INVALID,
          "short-tail terminal");
    check(aii_echo_finish(e, g, out.data(), &emitted) == 0 && emitted == n,
          "short-tail length");
    for (size_t i = 0; i < n; ++i)
      check(out[i] == near[i], "short-tail preserved");
    check(aii_echo_get_status(e, &s) == 0 && s.next_sample == n,
          "short-tail clock");
  }
  aii_echo_destroy(e);
  aii_echo_destroy(nullptr);
  std::cout
      << "native_echo_contracts PASS (synthetic, not browser qualification)\n";
}
