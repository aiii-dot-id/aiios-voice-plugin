#include "uid.h"
#include <algorithm>
#include <array>
#include <chrono>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

using Clock = std::chrono::steady_clock;
using Vector = std::array<double, 256>;
static std::vector<uint8_t> read(const std::string& path, size_t maximum) {
  std::ifstream f(path, std::ios::binary | std::ios::ate);
  if (!f) throw std::runtime_error("probe input unavailable");
  const auto extent = f.tellg();
  if (extent < 0 || static_cast<size_t>(extent) > maximum) throw std::runtime_error("probe input extent");
  std::vector<uint8_t> data(static_cast<size_t>(extent));
  f.seekg(0); f.read(reinterpret_cast<char*>(data.data()), static_cast<std::streamsize>(data.size()));
  if (!f) throw std::runtime_error("probe input short read");
  return data;
}
static double seconds(Clock::time_point start) { return std::chrono::duration<double>(Clock::now() - start).count(); }
static void require(bool yes, const char* why) { if (!yes) throw std::runtime_error(why); }

int main(int argc, char** argv) {
  try {
    if (argc != 4) throw std::invalid_argument("usage: aii_uid_probe MODEL PCM_PATHS BACKEND");
    char error[1024]{};
    auto model = read(argv[1], 100865597);
    const auto began = Clock::now();
    std::unique_ptr<AiiUid, decltype(&aii_uid_destroy)> uid(
        aii_uid_create(model.data(), model.size(), argv[3], error, sizeof(error)), aii_uid_destroy);
    if (!uid) throw std::runtime_error(error);
    const double construction = seconds(began);
    // The caller may release the original serialized buffer after create.
    std::fill(model.begin(), model.end(), 0); model.clear(); model.shrink_to_fit();
    std::ifstream list(argv[2]);
    require(bool(list), "probe corpus unavailable");
    std::string path;
    std::vector<uint8_t> first;
    Vector reference{};
    uint64_t id = 0;
    std::cout << std::setprecision(17);
    while (std::getline(list, path)) {
      if (!path.empty() && path.back() == '\r') path.pop_back();
      require(!path.empty() && id < 10000, "probe corpus line/count");
      auto pcm = read(path, 960000);
      Vector vector{};
      const auto begin = Clock::now();
      const int rc = aii_uid_embed(uid.get(), ++id, pcm.data(), pcm.size(), 16000,
          vector.data(), vector.size(), error, sizeof(error));
      if (rc) throw std::runtime_error(error);
      std::cout << "{\"index\":" << id - 1 << ",\"seconds\":" << seconds(begin) << ",\"samples\":" << pcm.size() / 2 << ",\"vector\":[";
      for (size_t i = 0; i < vector.size(); ++i) std::cout << (i ? "," : "") << vector[i];
      std::cout << "]}\n" << std::flush;
      if (id == 1) { first = pcm; reference = vector; }
    }
    require(!first.empty(), "probe corpus empty");
    Vector sentinel; sentinel.fill(19);
    auto unchanged = [&] { return std::all_of(sentinel.begin(), sentinel.end(), [](double x) { return x == 19; }); };
    auto refused = [&](uint64_t query, const std::vector<uint8_t>& pcm, int rate, size_t dimensions, int wanted) {
      const int rc = aii_uid_embed(uid.get(), query, pcm.data(), pcm.size(), rate, sentinel.data(), dimensions, error, sizeof(error));
      require(rc == wanted && unchanged() && aii_uid_phase(uid.get()) == 0, "invalid/suppressed input changed output or owner phase");
    };
    refused(0, first, 16000, 256, 1);
    refused(1, first, 16000, 256, 1);
    refused(20000, first, 8000, 256, 1);
    refused(20000, first, 16000, 255, 1);
    refused(20000, std::vector<uint8_t>(63839, 0), 16000, 256, 1);
    refused(20000, std::vector<uint8_t>(960002, 0), 16000, 256, 1);
    refused(20000, std::vector<uint8_t>(63840, 0), 16000, 256, 2);
    std::vector<uint8_t> clipped(63840);
    for (size_t i = 0; i < clipped.size(); i += 2) { clipped[i] = 255; clipped[i + 1] = 127; }
    refused(20001, clipped, 16000, 256, 2);
    std::vector<uint8_t> stress(960000);
    for (size_t i = 0; i < stress.size(); ++i) stress[i] = first[i % first.size()];
    int inference_rc = -1;
    std::thread worker([&] {
      inference_rc = aii_uid_embed(uid.get(), 30000, stress.data(), stress.size(), 16000,
          sentinel.data(), sentinel.size(), error, sizeof(error));
    });
    const auto wait = Clock::now();
    while (aii_uid_phase(uid.get()) != 2 && seconds(wait) < 5)
      std::this_thread::sleep_for(std::chrono::microseconds(50));
    // Keep the observed inference phase active for 20 ms before cancellation,
    // avoiding a proof consisting only of cancellation before Run enters.
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    const bool saw_inference = aii_uid_phase(uid.get()) == 2;
    char busy_error[128]{}; Vector busy_vector; busy_vector.fill(23);
    const int busy = aii_uid_embed(uid.get(), 30001, first.data(), first.size(), 16000,
        busy_vector.data(), busy_vector.size(), busy_error, sizeof(busy_error));
    const auto control = Clock::now();
    const int cancelled = aii_uid_cancel_through(uid.get(), 30000);
    const double control_seconds = seconds(control);
    worker.join();
    const double retirement_seconds = seconds(control);
    require(saw_inference && busy == 5 && std::all_of(busy_vector.begin(), busy_vector.end(), [](double x) { return x == 23; }), "concurrent inference admission escaped");
    require(cancelled == 0 && inference_rc == 3 && unchanged(), "cancelled inference published its vector");
    require(control_seconds < 0.05, "cancel waited behind inference");
    require(retirement_seconds < 0.25, "cancelled inference did not retire promptly");
    Vector recovery;
    require(aii_uid_embed(uid.get(), 30001, first.data(), first.size(), 16000, recovery.data(), recovery.size(), error, sizeof(error)) == 0,
        "UID did not recover after cancel");
    require(recovery == reference, "cancel recovery changed the exact vector");
    require(aii_uid_cancel_through(uid.get(), 30005) == 0 && aii_uid_cancel_through(uid.get(), 30003) == 0, "cancel fence failed");
    refused(30004, first, 16000, 256, 3);
    require(aii_uid_embed(uid.get(), 30006, first.data(), first.size(), 16000, recovery.data(), recovery.size(), error, sizeof(error)) == 0 && recovery == reference,
        "monotonic fence prevented fresh recovery");
    std::cout << "{\"summary\":true,\"count\":" << id << ",\"construction_seconds\":" << construction
              << ",\"invalid_refused\":true,\"cancel_seconds\":" << control_seconds
              << ",\"retirement_seconds\":" << retirement_seconds
              << ",\"recovery_exact\":true,\"monotonic_fence\":true}\n" << std::flush;
    return 0;
  } catch (const std::exception& e) { std::cerr << "UID probe: " << e.what() << '\n'; return 1; }
}
