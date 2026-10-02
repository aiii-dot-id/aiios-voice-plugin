#pragma once
#include <stddef.h>
#include <stdint.h>
#if defined(_WIN32)
#if defined(AII_VAD_BUILD)
#define AII_VAD_API __declspec(dllexport)
#else
#define AII_VAD_API __declspec(dllimport)
#endif
#else
#define AII_VAD_API __attribute__((visibility("default")))
#endif
#ifdef __cplusplus
extern "C" {
#endif
typedef struct AiiVad AiiVad;
// Private engine ABI, not an SDK extension. The existing asset owner must
// verify immutable checkpoint bytes before create. No fetch or model fallback.
// Set ORT_DISABLE_TELEMETRY=1 before ORT initialization. One control-thread
// owner calls feed/reset/destroy; none wait for the STT/TTS model owners.
AII_VAD_API AiiVad* aii_vad_create(const void*, size_t, char*, size_t);
AII_VAD_API int aii_vad_feed(AiiVad*, const float*, size_t, float*, char*, size_t);
AII_VAD_API int aii_vad_reset(AiiVad*, char*, size_t);
AII_VAD_API uint64_t aii_vad_samples(const AiiVad*);
AII_VAD_API void aii_vad_destroy(AiiVad*);
#ifdef __cplusplus
}
#endif
