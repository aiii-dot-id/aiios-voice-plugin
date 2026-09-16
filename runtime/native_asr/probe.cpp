// Recorded-PCM native component proof. This is NOT the resident SDK engine.
// Reads no expected text, saved logits, Python modules, or reference features.
#include "asr.h"
#include "frontend.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;
double elapsed(Clock::time_point start) { return std::chrono::duration<double>(Clock::now()-start).count(); }
std::vector<float> read(const fs::path& p, size_t max_count) {
  if (fs::is_symlink(p) || !fs::is_regular_file(p)) throw std::runtime_error("regular proof input required");
  const auto bytes = fs::file_size(p);
  if (bytes == 0 || bytes%4 || bytes/4 > max_count) throw std::runtime_error("bounded float input required");
  std::vector<float> data(bytes/4);
  std::ifstream f(p, std::ios::binary);
  f.read(reinterpret_cast<char*>(data.data()), bytes);
  if (!f || f.peek() != EOF) throw std::runtime_error("short or changed input");
  return data;
}
void write(const fs::path& p, const std::vector<float>& data) {
  if (fs::exists(p)) throw std::runtime_error("fresh proof output required");
  std::ofstream f(p, std::ios::binary);
  f.write(reinterpret_cast<const char*>(data.data()), data.size()*4);
  f.close();
  if (!f) throw std::runtime_error("write failed");
}
void check(int rc, const char* err) { if (rc != 0) throw std::runtime_error(std::string("native rc=")+std::to_string(rc)+": "+err); }
std::string quote(const std::string& s) {
  std::ostringstream out; out << '"';
  for (const auto c : s) {
    const auto u = static_cast<unsigned char>(c);
    if (c == '"' || c == '\\') out << '\\' << c;
    else if (u < 32) out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << unsigned(u) << std::dec;
    else out << c;
  }
  return out.str() + '"';
}
struct Stream {
  AiiAsrStream* s;
  char error[1024]{};
  explicit Stream(AiiAsr* m): s(aii_asr_stream_create(m, error, sizeof(error))) {
    if (!s) throw std::runtime_error(error);
  }
  ~Stream() { if (s) aii_asr_stream_destroy(s, error, sizeof(error)); }
  void accept(const float* p, size_t n) { check(aii_asr_accept(s, p, n, error, sizeof(error)), error); }
  int step() { return aii_asr_step(s, error, sizeof(error)); }
  std::string result() {
    char text[131072]{};
    check(aii_asr_result(s, text, sizeof(text), error, sizeof(error)), error);
    return text;
  }
};
void features(const std::vector<float>& mel, const fs::path& input, const fs::path& output, size_t packet) {
  auto pcm = read(input, 16000*60);
  aii::asr::Frontend bank(mel.data(), mel.size());
  std::vector<float> result;
  size_t first = 0;
  auto drain = [&] {
    while (first < bank.frames_ready()) {
      size_t count = std::min(size_t(128), bank.frames_ready()-first);
      auto rows = bank.frames(first, count);
      result.insert(result.end(), rows.begin(), rows.end()); first += count;
    }
  };
  for (size_t i = 0; i < pcm.size(); i += packet) {
    bank.accept(pcm.data()+i, std::min(packet, pcm.size()-i)); drain();
  }
  bank.finish(); drain(); write(output, result);
  std::cout << "{\"frames\":" << first << ",\"samples\":" << pcm.size() << "}\n";
}
void transcribe(AiiAsr* model, const fs::path& file, size_t packet) {
  auto pcm = read(file, 16000*30*60);
  Stream stream(model);
  const auto start = Clock::now();
  double first_partial = -1;
  std::vector<std::pair<size_t, std::string>> trajectory;
  uint64_t retained_peak=0;
  auto observe_buffer=[&] {
    AiiAsrBufferStats b{};
    check(aii_asr_buffer_stats(stream.s,&b,stream.error,sizeof stream.error),stream.error);
    if(b.first_sample+b.retained_samples!=b.total_samples)throw std::runtime_error("buffer clock differs");
    retained_peak=std::max(retained_peak,b.retained_samples);
  };
  auto decode = [&] {
    for (;;) {
      const auto rc = stream.step();
      if (rc == 0) break;
      if (rc != 1) check(rc, stream.error);
      auto text = stream.result();
      if (!text.empty() && first_partial < 0) first_partial = elapsed(start);
      AiiAsrStats stats{};
      check(aii_asr_stats(stream.s, &stats, stream.error, sizeof(stream.error)), stream.error);
      trajectory.emplace_back(stats.processed_frames, std::move(text));
      observe_buffer();
    }
  };
  for (size_t i = 0; i < pcm.size(); i += packet) {
    stream.accept(pcm.data()+i, std::min(packet, pcm.size()-i)); observe_buffer(); decode();
  }
  const auto finishing = Clock::now();
  check(aii_asr_finish(stream.s, stream.error, sizeof(stream.error)), stream.error); observe_buffer(); decode();
  const auto finish_seconds = elapsed(finishing);
  AiiAsrStats stats{};
  check(aii_asr_stats(stream.s, &stats, stream.error, sizeof(stream.error)), stream.error);
  if (!stats.input_finished || !stats.exhausted || stats.source_samples != pcm.size() ||
      stats.model_padding != 10560 || stats.cancelled) throw std::runtime_error("completion facts differ");
  std::cout << "{\"type\":\"transcript\",\"input\":" << quote(file.string())
    << ",\"text\":" << quote(stream.result()) << ",\"samples\":" << stats.source_samples
    << ",\"model_padding\":" << stats.model_padding << ",\"tokens\":" << stats.tokens
    << ",\"retained_peak_samples\":" << retained_peak
    << ",\"exhausted\":true,\"seconds\":" << elapsed(start)
    << ",\"first_partial_unpaced_seconds\":" << first_partial
    << ",\"finish_seconds\":" << finish_seconds << ",\"trajectory\":[";
  bool comma = false;
  for (const auto& [frame, text] : trajectory) {
    if (comma) std::cout << ',';
    comma = true;
    std::cout << "{\"processed_frames\":" << frame << ",\"text\":" << quote(text) << '}';
  }
  std::cout << "]}\n" << std::flush;
}
void cancel(AiiAsr* model, const fs::path& file) {
  auto pcm = read(file, 16000*60);
  Stream stream(model);
  stream.accept(pcm.data(), std::min(size_t(32000), pcm.size()));
  std::atomic<bool> done{false};
  int rc = -10;
  std::thread owner([&] { rc = stream.step(); done.store(true); });
  const auto started = Clock::now();
  while (!done.load() && aii_asr_phase(stream.s) != 2 && elapsed(started) < 10) std::this_thread::yield();
  const bool inference_entered = aii_asr_phase(stream.s) == 2;
  const auto begin = Clock::now();
  aii_asr_cancel(stream.s);
  const double admission = elapsed(begin);
  owner.join();
  const double retirement = elapsed(begin);
  char text[64] = "stale sentinel";
  const int result_rc = aii_asr_result(stream.s, text, sizeof(text), stream.error, sizeof(stream.error));
  if (!inference_entered || rc != 2 || result_rc != 2 || text[0] || aii_asr_busy(stream.s))
    throw std::runtime_error("in-flight cancel did not suppress result");
  // A later result retrieval must not revive this generation.
  const int later = aii_asr_step(stream.s, stream.error, sizeof(stream.error));
  if (later != 2) throw std::runtime_error("cancelled generation revived");
  std::cout << "{\"type\":\"cancel\",\"inference_entered\":true,\"result_suppressed\":true,"
    << "\"cancel_seconds\":" << admission << ",\"retirement_seconds\":" << retirement << "}\n" << std::flush;
}
int main(int argc, char** argv) {
  AiiAsr* model = nullptr;
  try {
    std::cout << std::setprecision(17);
    const uint16_t endian = 1;
    if (*reinterpret_cast<const uint8_t*>(&endian) != 1) throw std::runtime_error("little endian probe required");
    if (argc == 7 && std::string(argv[1]) == "features") {
      const auto packet = std::stoul(argv[6]);
      if (!packet || packet > 32000 || std::string(argv[5]) != "f32") throw std::runtime_error("feature packet/format");
      features(read(argv[2], 128*257), argv[3], argv[4], packet); return 0;
    }
    if ((argc != 4 && argc != 5) || std::string(argv[1]) != "recognize") throw std::runtime_error("recognize MODEL MEL [EXECUTION_JSON] or features MEL PCM OUTPUT f32 PACKET");
    const auto mel = read(argv[3], 128*257);
    char error[2048]{};
    const auto start = Clock::now();
    model = aii_asr_create_configured(argv[2], mel.data(), mel.size(), 4, argc==5?argv[4]:nullptr, error, sizeof(error));
    if (!model) throw std::runtime_error(error);
    char execution[4096]{};size_t required=0;
    check(aii_asr_execution_info(model,execution,sizeof execution,&required,error,sizeof error),error);
    std::cout << "{\"type\":\"ready\",\"execution\":" << execution << ",\"runtime\":"
      << quote(aii_asr_runtime_version()) << ",\"seconds\":" << elapsed(start) << "}\n" << std::flush;
    std::string line;
    while (std::getline(std::cin, line)) {
      if (line.size() > 8192) throw std::runtime_error("proof command bound");
      std::istringstream in(line);
      std::string op, file, tail;
      size_t packet = 0;
      in >> op;
      if (op == "quit") { if (in >> tail) throw std::runtime_error("extra fields"); break; }
      if (!(in >> std::quoted(file))) throw std::runtime_error("missing proof file");
      if (op == "transcribe") {
        if (!(in >> packet) || !packet || packet > 32000 || (in >> tail)) throw std::runtime_error("packet bound/extra fields");
        transcribe(model, file, packet);
      } else if (op == "cancel") {
        if (in >> tail) throw std::runtime_error("extra fields");
        cancel(model, file);
      } else throw std::runtime_error("unknown proof command");
    }
    aii_asr_destroy(model); return 0;
  } catch (const std::exception& e) {
    aii_asr_destroy(model);
    std::cerr << "native proof refused: " << e.what() << '\n'; return 1;
  }
}
