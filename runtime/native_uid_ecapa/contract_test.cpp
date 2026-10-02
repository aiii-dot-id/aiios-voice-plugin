#include "frontend.h"
#include "reference_test.h"
#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <iostream>
#include <stdexcept>
#include <thread>
#include <vector>

void require(bool condition) {
  if (!condition)
    throw std::runtime_error("frontend contract failed");
}
int cancel(void *p) {
  auto &n = *static_cast<int *>(p);
  return --n <= 0;
}
int main() {
  try {
    std::vector<uint8_t> pcm(64000, 0);
    std::vector<float> out(201 * 80, 99.0f);
    size_t frames = 999;
    require(aii_ecapa_fbank(pcm.data(), pcm.size(), 16000, out.data(),
                            out.size(), &frames, nullptr, nullptr) == 0);
    require(frames == 201 && std::all_of(out.begin(), out.end(),
                                         [](float v) { return v == 0; }));
    auto failure = [&](const uint8_t *p, size_t bytes, int rate,
                       size_t capacity, int expected,
                       aii_ecapa_cancelled cb = nullptr, void *ctx = nullptr) {
      std::fill(out.begin(), out.end(), 99.0f);
      frames = 999;
      require(aii_ecapa_fbank(p, bytes, rate, out.data(), capacity, &frames, cb,
                              ctx) == expected);
      require(frames == 0 && std::all_of(out.begin(), out.end(),
                                         [](float v) { return v == 99.0f; }));
    };
    failure(nullptr, 64000, 16000, out.size(), 1);
    failure(pcm.data(), 0, 16000, out.size(), 1);
    failure(pcm.data(), 3, 16000, out.size(), 1);
    failure(pcm.data(), 960002, 16000, out.size(), 1);
    failure(pcm.data(), 64000, 48000, out.size(), 1);
    failure(pcm.data(), 64000, 16000, out.size() - 1, 1);
    int polls = 1;
    failure(pcm.data(), 64000, 16000, out.size(), 3, cancel, &polls);
    polls = 55;
    failure(pcm.data(), 64000, 16000, out.size(), 3, cancel, &polls);
    require(polls == 0);
    require(aii_ecapa_fbank(pcm.data(), 64000, 16000, out.data(), out.size(),
                            nullptr, nullptr, nullptr) == 1);
    require(aii_ecapa_fbank(pcm.data(), 64000, 16000, nullptr, out.size(),
                            &frames, nullptr, nullptr) == 1);
    // A nonzero deterministic signal, exact at the PCM boundary.
    for (size_t i = 0; i < pcm.size(); i += 2) {
      pcm[i] = static_cast<uint8_t>(i);
      pcm[i + 1] = static_cast<uint8_t>(i / 251);
    }
    std::vector<float> expected(out.size());
    require(aii_ecapa_fbank(pcm.data(), pcm.size(), 16000, expected.data(),
                            expected.size(), &frames, nullptr, nullptr) == 0);
    std::atomic<bool> ok{true};
    std::array<std::thread, 4> jobs;
    for (auto &job : jobs)
      job = std::thread([&] {
        std::vector<float> result(expected.size());
        size_t n = 0;
        if (aii_ecapa_fbank(pcm.data(), pcm.size(), 16000, result.data(),
                            result.size(), &n, nullptr, nullptr) != 0 ||
            result != expected)
          ok = false;
      });
    for (auto &job : jobs)
      job.join();
    require(ok);
    // Independent upstream feature oracle, not a self-comparison.
    std::vector<uint8_t> reference_pcm(802);
    for (size_t i = 0; i < reference_pcm.size(); i += 2) {
      reference_pcm[i] = static_cast<uint8_t>(i);
      reference_pcm[i + 1] = static_cast<uint8_t>(i / 251);
    }
    std::array<float, 240> reference_output{};
    require(aii_ecapa_fbank(reference_pcm.data(), reference_pcm.size(), 16000,
                            reference_output.data(), reference_output.size(),
                            &frames, nullptr, nullptr) == 0);
    require(frames == 3);
    for (size_t i = 0; i < reference_output.size(); ++i)
      require(std::isfinite(reference_output[i]) &&
              std::abs(reference_output[i] - reference_features[i]) <= .003f);
    for (size_t samples :
         {size_t(1), size_t(159), size_t(160), size_t(161), size_t(399),
          size_t(400), size_t(401), size_t(480000)}) {
      std::vector<uint8_t> input(samples * 2, 0);
      std::vector<float> result((samples / 160 + 1) * 80);
      require(aii_ecapa_fbank(input.data(), input.size(), 16000, result.data(),
                              result.size(), &frames, nullptr, nullptr) == 0);
      require(frames == samples / 160 + 1 &&
              std::all_of(result.begin(), result.end(),
                          [](float v) { return v == 0; }));
    }
    std::cout << "ECAPA_FRONTEND_CONTRACTS_PASS\n";
  } catch (const std::exception &e) {
    std::cerr << e.what() << '\n';
    return 1;
  }
}
