// Private engine ABI. No SDK/public transport, devices, networking or credentials.
// The owner serializes start/next/reset/destroy. cancel/state never take its lock.
#include "engine/framework/runtime/registry.h"
#include "engine/framework/runtime/session.h"
#if defined(__linux__) && !defined(__ANDROID__)
#include "engine/framework/core/backend.h"
#include "engine/framework/io/json.h"
#include "vulkan_device_policy.h"
#endif

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>

#ifdef _WIN32
#define NV_EXPORT extern "C" __declspec(dllexport)
#else
#define NV_EXPORT extern "C" __attribute__((visibility("default")))
#endif

std::unique_ptr<engine::runtime::IVoiceTaskSession> nv_bound_session(
    const std::filesystem::path &, const std::filesystem::path &,
    const engine::runtime::SessionOptions &);

namespace {
using namespace engine::runtime;
constexpr int OK = 0, AUDIO = 1, ERROR = -1, CANCELLED = -2, BUSY = -3;
struct Resident {
    std::unique_ptr<ILoadedVoiceModel> model;
    std::unique_ptr<IVoiceTaskSession> base;
    IStreamingVoiceTaskSession * stream = nullptr;
    std::mutex owner;
    std::atomic<uint64_t> generation{0}, cancelled_through{0};
    std::atomic<bool> computing{false}, active{false}, faulted{false};
    size_t emitted = 0;
    std::string voice = "alba"; // pinned for this model session, never per segment
    std::filesystem::path voice_root;
    float temperature=.3f;
#if defined(__linux__) && !defined(__ANDROID__)
    std::string execution_info;
#endif
};

void message(char * output, size_t capacity, const char * text) noexcept {
    if (!output || !capacity) return;
    const auto count = std::min(capacity - 1, std::strlen(text));
    std::memcpy(output, text, count);
    output[count] = '\0';
}

std::string bounded(const char * value, size_t limit, const char * name) {
    if (!value) throw std::runtime_error(std::string(name) + " missing");
    size_t length = 0;
    while (length <= limit && value[length]) ++length;
    if (!length || length > limit) throw std::runtime_error(std::string(name) + " length refused");
    return std::string(value, length);
}

std::string voice_id(const char * value) {
    const auto id = bounded(value, 64, "voice ID");
    for (unsigned char c : id)
        if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-'))
            throw std::runtime_error("voice ID is not a preset name");
    return id;
}

TaskRequest request(const std::string & text, uint32_t seed, int limit, const char * noise,
                    const std::string & voice, float temperature=.3f) {
    TaskRequest r;
    r.text_input = Transcript{text, ""};
    r.voice = VoiceCondition{};
    r.voice->speaker = VoiceReference{};
    r.voice->speaker->cached_voice_id = voice;
    r.options["pocket_tts.temperature"] = std::to_string(temperature);
    r.options["pocket_tts.max_steps"] = std::to_string(limit);
    r.options["pocket_tts.seed"] = std::to_string(seed);
    r.options["text_chunk_size"] = "256";
    if (noise && noise[0]) r.options["pocket_tts.noise_file"] = bounded(noise, 4096, "noise path");
    return r;
}

bool cancelled(const Resident & h, uint64_t generation) noexcept {
    return h.cancelled_through.load() >= generation;
}

int fail(Resident & h, char * error, size_t capacity, const char * reason) noexcept {
    h.computing = false;
    h.active = false;
    h.faulted = true;
    message(error, capacity, reason);
    return ERROR;
}
}

static void * create(const char * assets, const char * config, const char * backend, int threads, const char * voice,
                     char * error, size_t error_capacity) noexcept {
    try {
        const std::string selected = bounded(backend, 16, "backend");
        if ((selected != "cpu" && selected != "vulkan" && selected != "metal") || threads < 1 || threads > 4)
            throw std::runtime_error("explicit cpu/vulkan/metal and 1..4 threads required");
        auto h = std::make_unique<Resident>();
        h->voice = voice_id(voice);
        const auto root = std::filesystem::u8path(bounded(assets, 4096, "asset path"));
        const auto voice_root = config ? root : root / "languages/english";
        h->voice_root = voice_root;
        if (!std::filesystem::is_regular_file(voice_root / "embeddings" / (h->voice + ".safetensors")))
            throw std::runtime_error("selected preset file is missing");
        SessionOptions options;
        options.backend.type = selected == "vulkan" ? engine::core::BackendType::Vulkan : engine::core::BackendType::Cpu;
        if(selected == "metal")options.backend.type = engine::core::BackendType::Metal;
        options.backend.threads = threads;
#if defined(__linux__) && !defined(__ANDROID__)
        h->execution_info = "{\"requested_backend\":\"" + selected + "\",\"hardware_execution_verified\":false}";
        if(selected == "vulkan") {
            const auto device = aii::pocket::prefer_vulkan_hardware(engine::core::list_backend_devices());
            options.backend.device = device.index;
            h->execution_info = "{\"requested_backend\":\"vulkan\",\"selection_policy\":\"prefer_discrete_then_integrated\","
                "\"device_index\":" + std::to_string(device.index) + ",\"device_name\":" +
                engine::io::json::stringify_string(device.name) + ",\"device_type\":" +
                engine::io::json::stringify_string(device.type) + ",\"hardware_execution_verified\":false}";
        }
#endif
        options.options["pocket_tts.matmul_weight_type"] = "f32";
        options.options["pocket_tts.conv_weight_type"] = "f32";
        if (config) {
            h->base = nv_bound_session(root, std::filesystem::u8path(bounded(config, 4096, "config path")), options);
        } else {
            auto registry = make_default_registry();
            ModelLoadRequest load;
            load.model_path = root;
            load.options["pocket_tts.language"] = "english_2026-04";
            h->model = registry.load(load);
            h->base = h->model->create_task_session({VoiceTaskKind::Tts, RunMode::Streaming}, options);
        }
        h->stream = dynamic_cast<IStreamingVoiceTaskSession *>(h->base.get());
        if (!h->stream) throw std::runtime_error("streaming interface unavailable");
        h->base->prepare(build_preparation_request(request("The local voice service is ready.", 20260908, 750, nullptr, h->voice)));
        return h.release();
    } catch (const std::exception & e) { message(error, error_capacity, e.what()); }
    catch (...) { message(error, error_capacity, "unknown native initialization failure"); }
    return nullptr;
}

NV_EXPORT void * nv_create(const char * assets, const char * backend, int threads,
                          char * error, size_t capacity) noexcept {
    return create(assets, nullptr, backend, threads, "alba", error, capacity);
}

NV_EXPORT void * nv_create_bound(const char * model_root, const char * config, const char * backend,
                                int threads, char * error, size_t capacity) noexcept {
    if (!config) { message(error, capacity, "bound config required"); return nullptr; }
    return create(model_root, config, backend, threads, "alba", error, capacity);
}

#if defined(__linux__) && !defined(__ANDROID__)
// Immutable selection readback, safe independently of the synthesis owner lock.
NV_EXPORT int nv_execution_info(void* opaque,char* output,size_t capacity) noexcept {
    if(!opaque || !output)return ERROR;
    const auto& text=static_cast<Resident*>(opaque)->execution_info;
    if(text.empty() || text.size()+1>capacity)return ERROR;
    std::memcpy(output,text.c_str(),text.size()+1);
    return OK;
}
#endif

// Private addition. Old callers/libraries keep the exact Alba contract. A new
// caller verifies the selected preset's immutable bytes before this entrypoint.
NV_EXPORT void * nv_create_voice_bound(const char * model_root, const char * config, const char * backend,
                                      int threads, const char * voice, char * error, size_t capacity) noexcept {
    if (!config) { message(error, capacity, "bound config required"); return nullptr; }
    return create(model_root, config, backend, threads, voice, error, capacity);
}

// Configure only a retired generation. Validation precedes mutation; no model
// reload or inference is needed, and one preset stays bound across all segments.
NV_EXPORT int nv_configure_voice(void* opaque,const char* voice,float temperature,char* error,size_t capacity) noexcept {
    if(!opaque)return ERROR;
    auto& h=*static_cast<Resident*>(opaque);
    std::unique_lock<std::mutex> lock(h.owner,std::try_to_lock);
    if(!lock.owns_lock() || h.active || h.computing)return BUSY;
    try {
        if(h.faulted)throw std::runtime_error("native engine faulted; reload required");
        const auto id=voice_id(voice);
        if(!std::isfinite(temperature) || temperature<0 || temperature>1)throw std::runtime_error("temperature outside [0,1]");
        if(!std::filesystem::is_regular_file(h.voice_root/"embeddings"/(id+".safetensors")))throw std::runtime_error("selected preset file is missing");
        h.voice=id;h.temperature=temperature;return OK;
    } catch(const std::exception& e) {message(error,capacity,e.what());return ERROR;}
      catch(...) {message(error,capacity,"native voice configuration failed");return ERROR;}
}

NV_EXPORT int nv_start(void * opaque, uint64_t generation, const char * text, uint32_t seed,
                       int limit, const char * noise, char * error, size_t capacity) noexcept {
    if (!opaque) return ERROR;
    auto & h = *static_cast<Resident *>(opaque);
    std::unique_lock<std::mutex> lock(h.owner, std::try_to_lock);
    if (!lock.owns_lock()) return BUSY;
    if (h.faulted) { message(error, capacity, "native engine faulted; reload required"); return ERROR; }
    if (!generation || generation <= h.generation || h.active || limit < 1 || limit > 750) {
        message(error, capacity, "generation, stream ownership or frame limit refused"); return ERROR;
    }
    try {
        const auto r = request(bounded(text, 2048, "text"), seed, limit, noise, h.voice,h.temperature);
        h.generation = generation;
        h.emitted = 0;
        if (cancelled(h, generation)) return CANCELLED;
        h.active = true;
        // Upstream owns seed/noise/frame-limit in its prepared request and
        // intentionally ignores their per-run overrides. Bind them here.
        h.computing = true;
        h.base->prepare(build_preparation_request(r));
        if (cancelled(h, generation)) { h.computing = false; h.stream->reset(); h.active = false; return CANCELLED; }
        h.stream->start_stream(r);
        h.computing = false;
        if (cancelled(h, generation)) { h.stream->reset(); h.active = false; return CANCELLED; }
        return OK;
    } catch (const std::exception & e) { return fail(h, error, capacity, e.what()); }
    catch (...) { return fail(h, error, capacity, "unknown native start failure"); }
}

NV_EXPORT int nv_next(void * opaque, uint64_t generation, float * pcm, size_t pcm_capacity,
                      size_t * samples, char * error, size_t error_capacity) noexcept {
    if (samples) *samples = 0;
    if (!opaque || !pcm || !samples || pcm_capacity < 1920 || pcm_capacity > 120000) return ERROR;
    auto & h = *static_cast<Resident *>(opaque);
    std::unique_lock<std::mutex> lock(h.owner, std::try_to_lock);
    if (!lock.owns_lock()) return BUSY;
    if (generation != h.generation) { message(error, error_capacity, "stale generation"); return ERROR; }
    if (h.faulted) { message(error, error_capacity, "native engine faulted; reload required"); return ERROR; }
    if (cancelled(h, generation)) return CANCELLED;
    if (!h.active) return OK;
    try {
        h.computing = true;
        auto event = h.stream->next_stream_event();
        h.computing = false;
        if (cancelled(h, generation)) {
            h.stream->reset(); h.active = false; return CANCELLED;
        }
        if (!event) {
            auto result = h.stream->finish_stream();
            if (!result.audio_output || result.audio_output->samples.size() != h.emitted || !h.emitted)
                throw std::runtime_error("natural completion lost its exact audio tail");
            h.active = false;
            return OK;
        }
        size_t count = 0;
        for (const auto & named : event->named_audio_outputs) {
            const auto & audio = named.audio;
            if (audio.sample_rate != 24000 || audio.channels != 1 || audio.samples.empty() ||
                audio.samples.size() > pcm_capacity - count)
                throw std::runtime_error("invalid or oversized native PCM chunk");
            for (float value : audio.samples) {
                if (!std::isfinite(value)) throw std::runtime_error("nonfinite native PCM");
                pcm[count++] = value;
            }
        }
        if (!count || count + h.emitted > 24000 * 60) throw std::runtime_error("native segment audio bound exceeded");
        // Last native publication fence. The caller retains its own delivery
        // fence for cancellation racing this return (the existing SDK rule).
        if (cancelled(h, generation)) { h.stream->reset(); h.active = false; return CANCELLED; }
        h.emitted += count;
        *samples = count;
        return AUDIO;
    } catch (const std::exception & e) { return fail(h, error, error_capacity, e.what()); }
    catch (...) { return fail(h, error, error_capacity, "unknown native inference failure"); }
}

NV_EXPORT int nv_cancel(void * opaque, uint64_t generation) noexcept {
    if (!opaque || !generation) return ERROR;
    auto & h = *static_cast<Resident *>(opaque);
    uint64_t old = h.cancelled_through.load();
    while (old < generation && !h.cancelled_through.compare_exchange_weak(old, generation)) {}
    return OK; // no owner lock, waiting, model call, allocation or event emission.
}

NV_EXPORT uint32_t nv_state(void * opaque) noexcept {
    if (!opaque) return 8;
    auto & h = *static_cast<Resident *>(opaque);
    return (h.computing ? 1u : 0u) | (h.active ? 2u : 0u) |
           (h.generation && cancelled(h, h.generation) ? 4u : 0u) | (h.faulted ? 8u : 0u);
}

NV_EXPORT int nv_reset(void * opaque, uint64_t generation, char * error, size_t capacity) noexcept {
    if (!opaque) return ERROR;
    auto & h = *static_cast<Resident *>(opaque);
    std::unique_lock<std::mutex> lock(h.owner, std::try_to_lock);
    if (!lock.owns_lock()) return BUSY;
    if (h.generation != generation) { message(error, capacity, "stale reset"); return ERROR; }
    try { h.stream->reset(); h.active = false; return OK; }
    catch (const std::exception & e) { return fail(h, error, capacity, e.what()); }
    catch (...) { return fail(h, error, capacity, "unknown reset failure"); }
}

NV_EXPORT int nv_destroy(void * opaque) noexcept {
    if (!opaque) return OK;
    auto * h = static_cast<Resident *>(opaque);
    std::unique_lock<std::mutex> lock(h->owner, std::try_to_lock);
    if (!lock.owns_lock() || h->active || h->computing) return BUSY;
    // Caller has retired ALL control/status users before destroy. No exported
    // operation accepts a freed pointer. Python's lifetime lock enforces this.
    lock.unlock();
    delete h;
    return OK;
}
