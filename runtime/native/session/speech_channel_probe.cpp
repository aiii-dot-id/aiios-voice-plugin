#include "model_channel.h"
#include "model_link.h"
#include "native_models.h"
#include "worker_environment.h"
#include <cstring>
#include <iostream>
#include <thread>
#include <csignal>
using namespace aii::voice;
namespace {
void check(bool ok, const std::string& why) { if (!ok) throw std::runtime_error(why); }
struct Voice { std::string voice; uint32_t seed; std::string text; };
std::vector<float> speak(Synthesizer& tts, const Voice& v, uint64_t id) {
  SpeechSettings s; s.voice = v.voice; s.seed = v.seed;
  tts.open(); tts.configure(s); tts.start(id, v.text);
  std::vector<float> all;
  for (;;) { auto part = tts.next(); if (part.empty()) break; all.insert(all.end(), part.begin(), part.end()); }
  tts.reset(); check(!all.empty(), "no speech"); return all;
}
struct Served final : StreamFactory {
  NativeModels& models;
  explicit Served(NativeModels& m) : models(m) {}
  std::unique_ptr<Synthesizer> speech() override { return models.open_speech(); }
  std::unique_ptr<Recognizer> recognizer() override { return models.open_recognizer(); }
  std::unique_ptr<Endpoint> endpoint() override { return models.open_endpoint(); }
};
}
int main(int argc, char** argv) {
  try {
    check(argc == 10, "asr mel vad endpoint coeff pocket config tts_backend asr_execution required");
    configure_worker_environment(std::string(argv[8]) == "vulkan");
    ModelPaths paths{argv[1], argv[2], argv[3], argv[4], argv[5], argv[6], argv[7]};
    paths.tts_backend = argv[8]; paths.asr_execution = argv[9];
    const Voice a{"alba", 20261009, "Good morning. The weather in Paris is clear and mild today."},
                b{"marius", 4242, "Remind me to call the garage on Thursday about the brakes."};
    NativeModels models(paths);
    auto in_process = models.open_speech();
    const auto direct_a = speak(*in_process, a, 1), direct_b = speak(*in_process, b, 2);
    in_process.reset();
    Served factory(models);
    std::signal(SIGPIPE, SIG_IGN);
    const auto ends = model_channel_pipes();
    std::thread server([&] { serve_model_channel(factory, ends.second); close_channel_end(ends.second); });
    bool alone_same = false, a_same = false, b_same = false;
    {
      ModelChannel channel(ends.first);
      auto remote = channel.speech();
      alone_same = speak(*remote, a, 1) == direct_a;
      auto ra = channel.speech(), rb = channel.speech();
      std::vector<float> together_a, together_b;
      std::thread ta([&] { together_a = speak(*ra, a, 1); });
      std::thread tb([&] { together_b = speak(*rb, b, 2); });
      ta.join(); tb.join();
      a_same = together_a == direct_a; b_same = together_b == direct_b;
    }
    server.join();
    std::cout << "{\"remote_alone_exactly_as_in_process\":" << (alone_same ? "true" : "false")
              << ",\"remote_a_beside_b_exactly_as_in_process\":" << (a_same ? "true" : "false")
              << ",\"remote_b_beside_a_exactly_as_in_process\":" << (b_same ? "true" : "false")
              << ",\"samples_a\":" << direct_a.size() << ",\"samples_b\":" << direct_b.size() << "}\n";
    return alone_same && a_same && b_same ? 0 : 2;
  } catch (const std::exception& e) { std::cerr << "speech channel probe failed: " << e.what() << '\n'; return 1; }
}
