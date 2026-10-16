// Private component proof, not an SDK guest or a production audio owner.
#include "engine/framework/runtime/registry.h"
#include "engine/framework/runtime/session.h"
#include "engine/framework/audio/wav_writer.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

using Clock = std::chrono::steady_clock;
using namespace engine::runtime;

double milliseconds(Clock::time_point start) {
    return std::chrono::duration<double, std::milli>(Clock::now() - start).count();
}

int main(int argc, char **argv) {
    try {
        if (argc != 5 && argc != 7)
            throw std::runtime_error("model noise text output [cpu|vulkan threads] required");
        const std::string backend = argc == 7 ? argv[5] : "cpu";
        const int threads = argc == 7 ? std::stoi(argv[6]) : 1;
        if ((backend != "cpu" && backend != "vulkan") || threads < 1 || threads > 4)
            throw std::runtime_error("unsupported scoped backend/threads");
        const auto initialized = Clock::now();
        auto registry = make_default_registry();
        ModelLoadRequest load;
        load.model_path = argv[1];
        load.options["pocket_tts.language"] = "english_2026-04";
        auto model = registry.load(load);
        SessionOptions options;
        options.backend.type = backend == "vulkan" ? engine::core::BackendType::Vulkan
                                                  : engine::core::BackendType::Cpu;
        options.backend.threads = threads;
        options.options["pocket_tts.matmul_weight_type"] = "f32";
        options.options["pocket_tts.conv_weight_type"] = "f32";
        auto base = model->create_task_session({VoiceTaskKind::Tts, RunMode::Streaming}, options);
        auto *session = dynamic_cast<IStreamingVoiceTaskSession *>(base.get());
        if (!session) throw std::runtime_error("streaming session unavailable");
        TaskRequest request;
        request.text_input = Transcript{argv[3], ""};
        request.voice = VoiceCondition{};
        request.voice->speaker = VoiceReference{};
        request.voice->speaker->cached_voice_id = "alba";
        request.options["pocket_tts.temperature"] = "0.3";
        request.options["pocket_tts.max_steps"] = "750";
        // The upstream streaming default reuses max_tokens=50 as a character
        // budget; offline defaults to 256. Bind the same segmentation policy
        // explicitly before comparing the same sampler schedule and tails.
        request.options["text_chunk_size"] = "256";
        request.options["pocket_tts.noise_file"] = argv[2];
        base->prepare(build_preparation_request(request));
        const double initialize_ms = milliseconds(initialized);
        const std::filesystem::path output = argv[4];
        std::filesystem::create_directory(output);
        std::vector<float> baseline;
        std::cout << "{\"initialize_ms\":" << initialize_ms
                  << ",\"backend\":\"" << backend << "\",\"threads\":" << threads
                  << ",\"runs\":[";
        for (int run = 0; run < 3; ++run) {
            auto start = Clock::now();
            session->start_stream(request);
            std::vector<float> audio;
            double first_ms = 0;
            size_t chunks = 0;
            while (auto event = session->next_stream_event()) {
                for (const auto &named : event->named_audio_outputs) {
                    const auto &chunk = named.audio;
                    if (chunk.sample_rate != 24000 || chunk.channels != 1 || chunk.samples.empty())
                        throw std::runtime_error("invalid PCM chunk");
                    if (chunks++ == 0) first_ms = milliseconds(start);
                    audio.insert(audio.end(), chunk.samples.begin(), chunk.samples.end());
                }
                if (run == 1 && chunks) break;
            }
            bool reset_refused_stale = false;
            double reset_ms = 0;
            if (run == 1) {
                // Reset only on the inference owner, between pulls. This does
                // NOT certify concurrent barge-in or browser playback fencing.
                auto reset_start = Clock::now();
                session->reset();
                reset_ms = milliseconds(reset_start);
                try { (void)session->next_stream_event(); }
                catch (const std::runtime_error &) { reset_refused_stale = true; }
                if (!reset_refused_stale) throw std::runtime_error("cancelled stream emitted again");
            } else {
                auto tail = session->finish_stream();
                if (!tail.audio_output || tail.audio_output->samples != audio)
                    throw std::runtime_error("stream result lost or duplicated a tail");
                // Retain the complete recovery even when its equality gate
                // fails, so the failed candidate can be diagnosed from bytes.
                engine::audio::write_pcm16_wav(output / (std::to_string(run) + ".wav"), 24000, 1, audio);
                if (run == 0) baseline = audio;
                else if (audio != baseline) {
                    double largest = 0.0, error_energy = 0.0, baseline_energy = 0.0;
                    if (audio.size() == baseline.size()) {
                        for (size_t i = 0; i < audio.size(); ++i) {
                            const double delta = double(audio[i]) - double(baseline[i]);
                            largest = std::max(largest, std::abs(delta));
                            error_energy += delta * delta;
                            baseline_energy += double(baseline[i]) * double(baseline[i]);
                        }
                    }
                    std::cerr << "recovery comparison: baseline_samples=" << baseline.size()
                              << " recovery_samples=" << audio.size() << " max_delta=" << largest
                              << " relative_error_energy=" << error_energy / std::max(1e-30, baseline_energy) << '\n';
                    throw std::runtime_error("recovery audio changed");
                }
            }
            if (run) std::cout << ',';
            std::cout << "{\"index\":" << run << ",\"samples\":" << audio.size()
                      << ",\"chunks\":" << chunks << ",\"first_pcm_ms\":" << first_ms
                      << ",\"wall_ms\":" << milliseconds(start) << ",\"reset_ms\":" << reset_ms
                      << ",\"stale_pull_refused\":" << (reset_refused_stale ? "true" : "false") << '}';
        }
        std::cout << "],\"passed\":true}\n";
        return 0;
    } catch (const std::exception &e) {
        std::cerr << "stream proof failed: " << e.what() << '\n';
        return 1;
    }
}
