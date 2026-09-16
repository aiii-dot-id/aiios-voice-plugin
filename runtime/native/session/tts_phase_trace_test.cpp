#include "tts_phase_trace.h"
#include <stdexcept>
#include <string>
#include <iostream>
namespace {
unsigned calls = 0;
int query(uint64_t* out, size_t count, int reset) {
  if (reset || count != 4) throw std::runtime_error("trace reset global counters");
  ++calls;
  for (size_t i = 0; i < count; ++i) out[i] = calls * 10 + i;
  return 0;
}
void require(bool value, const char* message) {
  if (!value) throw std::runtime_error(message);
}
std::string read(FILE* f) {
  std::rewind(f); std::string result; char b[1024];
  while (const auto n = std::fread(b, 1, sizeof b, f)) result.append(b, n);
  return result;
}
}
int main() {
  try {
    FILE* f = std::tmpfile(); require(f, "test output unavailable");
    aii::voice::TtsPhaseTrace disabled(false, query, f);
    disabled.begin(1, 1); disabled.started(0); disabled.audio(1920); disabled.finish();
    require(calls == 0 && std::ftell(f) == 0, "disabled trace touched model or output");
    aii::voice::TtsPhaseTrace trace(true, query, f);
    trace.begin(4, 11); trace.started(0); trace.audio(0); trace.audio(1920); trace.audio(1920);
    require(calls == 3, "first output snapshot was overwritten");
    require(std::ftell(f) == 0, "trace wrote before retirement");
    trace.finish(); trace.finish(); require(calls == 4, "duplicate retirement sampled");
    auto first = read(f);
    require(first.find("\"available\":true") != std::string::npos, "snapshot unavailable");
    require(first.find("\"first\":[30,31,32,33]") != std::string::npos, "first counters changed");
    std::fseek(f, 0, SEEK_END);
    trace.begin(5, 12); trace.started(-2); trace.finish();
    auto both = read(f);
    const auto at = both.find("\"generation\":12"); require(at != std::string::npos, "generation absent");
    require(both.substr(at).find("\"first_ns\":0") != std::string::npos, "cancelled generation inherited first PCM");
    require(both.substr(at).find("\"first\":[0,0,0,0]") != std::string::npos, "cancelled generation inherited counters");
    std::cout << both; std::fclose(f);
    FILE* missing = std::tmpfile(); require(missing, "test output unavailable");
    aii::voice::TtsPhaseTrace absent(true, nullptr, missing);
    absent.begin(1, 1); absent.started(0); absent.audio(1920); absent.finish();
    require(read(missing).find("\"available\":false") != std::string::npos, "missing counter falsely certified");
    std::fclose(missing);
    std::cout << "native TTS phase ownership PASS\n";
    return 0;
  } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
