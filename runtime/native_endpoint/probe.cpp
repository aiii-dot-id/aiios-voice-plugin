#include "endpoint.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

using Clock = std::chrono::steady_clock;
static double elapsed(Clock::time_point start) { return std::chrono::duration<double>(Clock::now()-start).count(); }
static void require(bool yes, const char* why) { if (!yes) throw std::runtime_error(why); }
template<class T> static std::vector<T> read(const std::string& path, size_t maximum) {
  std::ifstream f(path, std::ios::binary | std::ios::ate);
  require(bool(f), "native endpoint fixture missing");
  const auto end = f.tellg();
  require(end > 0 && static_cast<size_t>(end) <= maximum && end % sizeof(T) == 0, "fixture size differs");
  std::vector<T> data(static_cast<size_t>(end)/sizeof(T));
  f.seekg(0); f.read(reinterpret_cast<char*>(data.data()), end);
  require(bool(f), "short fixture read"); return data;
}
int main(int argc, char** argv) {
  try {
    require(argc == 5, "usage: endpoint_probe MODEL COEFFICIENTS INPUT_LIST FEATURE_DIR");
    auto model = read<unsigned char>(argv[1], 32411198);
    auto coefficients = read<float>(argv[2], 65920);
    char error[1024]{};
    const auto begin = Clock::now();
    std::unique_ptr<AiiEndpoint, decltype(&aii_endpoint_destroy)> owner(
      aii_endpoint_create(model.data(), model.size(), coefficients.data(), coefficients.size(), error, sizeof(error)), aii_endpoint_destroy);
    if (!owner) throw std::runtime_error(error);
    const double construction = elapsed(begin);
    std::fill(model.begin(), model.end(), 0); model.clear(); model.shrink_to_fit();
    std::fill(coefficients.begin(), coefficients.end(), 0); coefficients.clear(); coefficients.shrink_to_fit();
    std::ifstream inputs(argv[3]); require(bool(inputs), "input list missing");
    uint64_t id = 0;
    std::string path;
    std::vector<float> first, reference_features;
    double reference = -1;
    std::cout << std::setprecision(17);
    while (std::getline(inputs, path)) {
      if (!path.empty() && path.back() == '\r') path.pop_back();
      require(!path.empty() && id < 10000, "input list geometry");
      auto pcm = read<float>(path, 960000*4);
      std::vector<float> features(64000);
      double p = -1;
      const auto began = Clock::now();
      const int rc = aii_endpoint_score(owner.get(), ++id, pcm.data(), pcm.size(), &p, features.data(), features.size(), error, sizeof(error));
      if (rc) throw std::runtime_error(error);
      const double seconds = elapsed(began);
      std::ofstream out(std::string(argv[4])+"/"+std::to_string(id-1)+".f32", std::ios::binary);
      out.write(reinterpret_cast<const char*>(features.data()), static_cast<std::streamsize>(features.size()*4));
      out.close(); require(bool(out), "feature write failed");
      std::cout << "{\"index\":" << id-1 << ",\"samples\":" << pcm.size() << ",\"probability\":" << p
                << ",\"seconds\":" << seconds << "}\n" << std::flush;
      if (id == 1) { first = pcm; reference = p; reference_features = features; }
    }
    require(id > 0, "empty endpoint panel");
    double sentinel = 19;
    std::vector<float> output(64000, 23);
    auto refused = [&](uint64_t query, const float* pcm, size_t count, size_t capacity) {
      const int rc = aii_endpoint_score(owner.get(), query, pcm, count, &sentinel, output.data(), capacity, error, sizeof(error));
      require(rc == 1 && sentinel == 19 && std::all_of(output.begin(), output.end(), [](float x) { return x == 23; }), "invalid query changed output");
    };
    refused(0, first.data(), first.size(), 64000);
    refused(1, first.data(), first.size(), 64000);
    refused(20000, first.data(), 0, 64000);
    refused(20000, first.data(), 960001, 64000);
    refused(20000, first.data(), first.size(), 63999);
    const float invalid[] = {std::numeric_limits<float>::quiet_NaN()};
    refused(20000, invalid, 1, 64000);
    int rc = -1;
    std::thread worker([&] { rc = aii_endpoint_score(owner.get(), 30000, first.data(), first.size(), &sentinel, output.data(), output.size(), error, sizeof(error)); });
    const auto wait = Clock::now();
    while (aii_endpoint_phase(owner.get()) != 2 && elapsed(wait) < 5) std::this_thread::sleep_for(std::chrono::microseconds(10));
    const bool inference = aii_endpoint_phase(owner.get()) == 2;
    const auto cancel = Clock::now();
    const int admission = aii_endpoint_cancel_through(owner.get(), 30000);
    const double control_seconds = elapsed(cancel);
    worker.join();
    const double retirement = elapsed(cancel);
    require(inference && admission == 0 && rc == 3 && sentinel == 19 &&
      std::all_of(output.begin(), output.end(), [](float x) { return x == 23; }), "cancelled endpoint published a decision");
    require(control_seconds < .05 && retirement < .25, "endpoint cancellation delayed");
    double recovery = -1;
    rc = aii_endpoint_score(owner.get(), 30001, first.data(), first.size(), &recovery, output.data(), output.size(), error, sizeof(error));
    require(rc == 0 && recovery == reference && output == reference_features, "endpoint recovery changed");
    require(aii_endpoint_cancel_through(owner.get(), 30005) == 0 && aii_endpoint_cancel_through(owner.get(), 30003) == 0, "monotonic cancellation failed");
    sentinel = 19;
    require(aii_endpoint_score(owner.get(), 30004, first.data(), first.size(), &sentinel, nullptr, 0, error, sizeof(error)) == 3 && sentinel == 19, "cancel fence decreased");
    require(aii_endpoint_score(owner.get(), 30006, first.data(), first.size(), &recovery, nullptr, 0, error, sizeof(error)) == 0 && recovery == reference, "fresh endpoint recovery failed");
    std::cout << "{\"summary\":true,\"count\":" << id << ",\"construction_seconds\":" << construction
              << ",\"cancel_seconds\":" << control_seconds << ",\"retirement_seconds\":" << retirement
              << ",\"recovery_exact\":true,\"invalid_refused\":true}\n";
    return 0;
  } catch (const std::exception& e) { std::cerr << "endpoint probe: " << e.what() << '\n'; return 1; }
}
