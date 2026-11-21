#include "model_channel.h"
#include "model_link.h"
#include "native_speaker.h"
#include "uid_backend.h"
#include "worker_environment.h"
#include "../../native_uid/bound_policies.h"
#include <cstring>
#include <fstream>
#include <iostream>
#include <thread>
#include <csignal>
using namespace aii::voice;
namespace {
void check(bool ok, const std::string& why) { if (!ok) throw std::runtime_error(why); }
std::string bytes(const char* path, size_t bound) {
  std::ifstream f(path, std::ios::binary | std::ios::ate); const auto n = f.tellg();
  check(bool(f) && n > 0 && uint64_t(n) <= bound, std::string("bounded input required: ") + path);
  std::string v(static_cast<size_t>(n), '\0'); f.seekg(0); f.read(v.data(), n); check(bool(f), "read"); return v;
}
std::vector<float> pcm(const char* path) {
  const auto raw = bytes(path, 16000ull * 60 * 4); check(raw.size() % 4 == 0, "float PCM required");
  std::vector<float> v(raw.size() / 4); std::memcpy(v.data(), raw.data(), raw.size()); return v;
}
struct Served final : StreamFactory {
  std::shared_ptr<SpeakerModel> model;
  explicit Served(std::shared_ptr<SpeakerModel> m) : model(std::move(m)) {}
  std::unique_ptr<Synthesizer> speech() override { throw std::logic_error("this probe serves the speaker only"); }
  std::unique_ptr<Recognizer> recognizer() override { throw std::logic_error("this probe serves the speaker only"); }
  std::unique_ptr<Endpoint> endpoint() override { throw std::logic_error("this probe serves the speaker only"); }
  std::unique_ptr<SpeakerEmbedder> speaker() override { return local_speaker_embedder(model); }
};
}
int main(int argc, char** argv) {
  try {
    check(argc == 5, "uid_model policy_json pcm_a pcm_b required");
    configure_worker_environment(false);
    const aii::uid::BoundPolicies policies(bytes(argv[2], 4096), std::string{});
    const auto policy = policies.current().policy;
    const auto a = pcm(argv[3]), b = pcm(argv[4]);
    const NativeSpeaker::ReadSnapshot no_storage = []() -> aii::uid::Snapshot { throw std::logic_error("this probe reads no storage"); };
    NativeSpeaker local(argv[1], std::string(uid_backend), policy, no_storage);
    local.warm();
    const auto alone_a = local.prepare_capture(a), alone_b = local.prepare_capture(b);
    Served factory(local.model());
#ifdef SIGPIPE
    std::signal(SIGPIPE, SIG_IGN);
#endif
    const auto ends = model_channel_pipes();
    std::thread server([&] { serve_model_channel(factory, ends.second); close_channel_end(ends.second); });
    bool alone = false, same_a = false, same_b = false, warmed = false; std::string fault_a, fault_b;
    {
      ModelChannel channel(ends.first);
      {
        NativeSpeaker remote(channel.speaker(), policy, no_storage);
        remote.warm(); warmed = true;
        const auto got = remote.prepare_capture(a);
        alone = got.embedding == alone_a.embedding && got.audio_sha256 == alone_a.audio_sha256;
      }
      NativeSpeaker first(channel.speaker(), policy, no_storage), second(channel.speaker(), policy, no_storage);
      aii::uid::Sample together_a, together_b;
      std::thread ta([&] { try { for (int i = 0; i < 3; ++i) together_a = first.prepare_capture(a); } catch (const std::exception& e) { fault_a = e.what(); } });
      std::thread tb([&] { try { for (int i = 0; i < 3; ++i) together_b = second.prepare_capture(b); } catch (const std::exception& e) { fault_b = e.what(); } });
      ta.join(); tb.join();
      same_a = together_a.embedding == alone_a.embedding; same_b = together_b.embedding == alone_b.embedding;
    }
    server.join();
    check(fault_a.empty(), "speaker A over the channel: " + fault_a); check(fault_b.empty(), "speaker B over the channel: " + fault_b);
    std::cout << "{\"backend\":\"" << uid_backend << "\",\"embedding_dimensions\":" << alone_a.embedding.size()
              << ",\"warm_over_the_channel\":" << (warmed ? "true" : "false")
              << ",\"remote_alone_exactly_as_in_process\":" << (alone ? "true" : "false")
              << ",\"remote_a_beside_b_exactly_as_in_process\":" << (same_a ? "true" : "false")
              << ",\"remote_b_beside_a_exactly_as_in_process\":" << (same_b ? "true" : "false")
              << ",\"a_differs_from_b\":" << (alone_a.embedding != alone_b.embedding ? "true" : "false") << "}\n";
    return alone && same_a && same_b ? 0 : 2;
  } catch (const std::exception& e) { std::cerr << "speaker channel probe failed: " << e.what() << '\n'; return 1; }
}
