#include "profiling.h"
#include <cstddef>

namespace native_voice_profile {
std::atomic<uint64_t> prepare_ns{0}, acoustic_ns{0}, decoder_ns{0}, decoded_frames{0};
}

// Snapshot only at an idle model-owner boundary. Counts are cumulative across
// sessions in this diagnostic DLL; the caller resets before its own run.
#if defined(_WIN32)
#define NV_PROFILE_EXPORT extern "C" __declspec(dllexport)
#else
#define NV_PROFILE_EXPORT extern "C" __attribute__((visibility("default")))
#endif
NV_PROFILE_EXPORT int nv_profile_snapshot(uint64_t* out, size_t count, int reset) {
    if (!out || count != 4 || (reset != 0 && reset != 1)) return 1;
    using namespace native_voice_profile;
    std::atomic<uint64_t>* counters[] = {&prepare_ns, &acoustic_ns, &decoder_ns, &decoded_frames};
    for (size_t i = 0; i < 4; ++i)
        out[i] = reset ? counters[i]->exchange(0) : counters[i]->load();
    return 0;
}
