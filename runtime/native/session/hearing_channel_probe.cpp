#include "model_channel.h"
#include "model_link.h"
#include "native_models.h"
#include "worker_environment.h"
#include <chrono>
#include <fstream>
#include <iostream>
#include <thread>
#include <csignal>
using namespace aii::voice;
using Clock = std::chrono::steady_clock;
using namespace std::chrono_literals;
namespace {
void check(bool ok, const std::string& why) { if (!ok) throw std::runtime_error(why); }
std::vector<float> pcm(const char* path) {
  std::ifstream f(path, std::ios::binary | std::ios::ate); const auto n = f.tellg();
  check(bool(f) && n > 0 && n % 4 == 0 && uint64_t(n) <= 16000ull * 60 * 4, "bounded float PCM required");
  std::vector<float> v(static_cast<size_t>(n) / 4); f.seekg(0); f.read(reinterpret_cast<char*>(v.data()), n);
  check(bool(f), "PCM read"); return v;
}
std::vector<std::string> hear(Recognizer& r, Vad& v, Endpoint& e, Synthesizer& tts, const std::vector<float>& input) {
  const auto begin = Clock::now(); std::vector<std::string> finals;
  Session session(r, v, e, tts, Settings{768, .5f});
  auto pump = [&] {
    const auto state = session.status(); check(state.error.empty(), state.error);
    check(std::chrono::duration<double>(Clock::now() - begin).count() < 180, "hearing deadline");
    Event ev; while (session.event(ev)) if (ev.kind == "transcript_final") finals.push_back(ev.text);
  };
  for (size_t offset = 0; offset < input.size();) {
    const auto n = std::min(size_t(512), input.size() - offset);
    if (session.feed(offset, input.data() + offset, n)) offset += n;
    pump(); std::this_thread::sleep_for(1ms);
  }
  session.finish_input(input.size());
  while (!session.status().input_finished) { pump(); std::this_thread::sleep_for(1ms); }
  pump(); session.close(false); check(session.wait_closed(30000), "session did not retire"); pump();
  return finals;
}
struct Served final : StreamFactory {
  NativeModels& models;
  explicit Served(NativeModels& m) : models(m) {}
  std::unique_ptr<Synthesizer> speech() override { return models.open_speech(); }
  std::unique_ptr<Recognizer> recognizer() override { return models.open_recognizer(); }
  std::unique_ptr<Endpoint> endpoint() override { return models.open_endpoint(); }
};
std::string json(const std::vector<std::string>& finals) {
  std::string out = "[";
  for (size_t i = 0; i < finals.size(); ++i) { out += (i ? "," : ""); out += '"'; out += finals[i]; out += '"'; }
  return out + "]";
}
}
int main(int argc, char** argv) {
  try {
    check(argc == 11, "asr mel vad endpoint coeff pocket config asr_execution pcm_a pcm_b required");
    configure_worker_environment(false);
    ModelPaths paths{argv[1], argv[2], argv[3], argv[4], argv[5], argv[6], argv[7]}; paths.asr_execution = argv[8];
    const auto a = pcm(argv[9]), b = pcm(argv[10]);
    NativeModels models(paths);
    auto& tts = models.synthesizer();
    auto in_a = models.open_hearing(); const auto alone_a = hear(*in_a.recognizer, *in_a.vad, *in_a.endpoint, tts, a);
    auto in_b = models.open_hearing(); const auto alone_b = hear(*in_b.recognizer, *in_b.vad, *in_b.endpoint, tts, b);
    Served factory(models);
    std::signal(SIGPIPE, SIG_IGN);
    const auto ends = model_channel_pipes();
    std::thread server([&] { serve_model_channel(factory, ends.second); close_channel_end(ends.second); });
    std::vector<std::string> remote_a, together_a, together_b; std::string fault_a, fault_b;
    {
      ModelChannel channel(ends.first);
      {
        auto r = channel.recognizer(); auto e = channel.endpoint(); auto v = models.open_vad();
        remote_a = hear(*r, *v, *e, tts, a);
      }
      auto ra = channel.recognizer(), rb = channel.recognizer();
      auto ea = channel.endpoint(), eb = channel.endpoint();
      auto va = models.open_vad(), vb = models.open_vad();
      std::thread first([&] { try { together_a = hear(*ra, *va, *ea, tts, a); } catch (const std::exception& x) { fault_a = x.what(); } });
      std::thread second([&] { try { together_b = hear(*rb, *vb, *eb, tts, b); } catch (const std::exception& x) { fault_b = x.what(); } });
      first.join(); second.join();
    }
    server.join();
    check(fault_a.empty(), "session A over the channel: " + fault_a); check(fault_b.empty(), "session B over the channel: " + fault_b);
    const bool alone = remote_a == alone_a, same_a = together_a == alone_a, same_b = together_b == alone_b;
    std::cout << "{\"in_process_a\":" << json(alone_a) << ",\"in_process_b\":" << json(alone_b)
              << ",\"remote_alone_exactly_as_in_process\":" << (alone ? "true" : "false")
              << ",\"remote_a_beside_b_exactly_as_in_process\":" << (same_a ? "true" : "false")
              << ",\"remote_b_beside_a_exactly_as_in_process\":" << (same_b ? "true" : "false") << "}\n";
    return alone && same_a && same_b ? 0 : 2;
  } catch (const std::exception& e) { std::cerr << "hearing channel probe failed: " << e.what() << '\n'; return 1; }
}
