#pragma once
#include <stddef.h>
#include <stdint.h>
#if defined(_WIN32)
#define AII_ASR_API __declspec(dllexport)
#else
#define AII_ASR_API __attribute__((visibility("default")))
#endif
#ifdef __cplusplus
extern "C" {
#endif
typedef struct AiiAsr AiiAsr;
typedef struct AiiAsrStream AiiAsrStream;
typedef struct {
  uint64_t source_samples, model_padding, feature_frames, processed_frames, tokens;
  int input_finished, exhausted, cancelled, busy, failed;
} AiiAsrStats;
typedef struct { uint64_t first_sample, retained_samples, total_samples; } AiiAsrBufferStats;
// The caller supplies a verified, immutable model tree and verified mel bits.
// This private C ABI is not the Plugin SDK. No network or fallback provider.
// The process owner sets ORT_DISABLE_TELEMETRY=1 before loading/using ORT;
// creation refuses a missing/contradictory setting. Build-time removal remains
// the intended release recipe. The API suppressor alone is too late.
AII_ASR_API AiiAsr* aii_asr_create(const char* model_root, const float* mel,
    size_t mel_count, int threads, char* error, size_t error_capacity);
// Additive private ABI: explicit immutable JSON selection, never ambient env.
// Null keeps the original target profile. CPU and DirectML do not substitute.
AII_ASR_API AiiAsr* aii_asr_create_configured(const char* model_root, const float* mel,
    size_t mel_count, int threads, const char* execution_json, char* error, size_t error_capacity);
// Initialization facts, not a claim that every node ran on an accelerator.
// Required includes NUL; returns 1 on insufficient capacity without truncation.
AII_ASR_API int aii_asr_execution_info(AiiAsr*, char*, size_t, size_t*, char*, size_t);
AII_ASR_API void aii_asr_destroy(AiiAsr* model);
AII_ASR_API AiiAsrStream* aii_asr_stream_create(AiiAsr*, char*, size_t);
// Exactly one owner calls stream functions, except cancel and busy which may
// run on the interruption lane. Retire the owner before destroying its stream.
AII_ASR_API int aii_asr_accept(AiiAsrStream*, const float*, size_t, char*, size_t);
AII_ASR_API int aii_asr_finish(AiiAsrStream*, char*, size_t);
// step: 1 = decoded one window; 0 = not ready/exhausted; 2 = cancelled; -1 = fault.
AII_ASR_API int aii_asr_step(AiiAsrStream*, char*, size_t);
AII_ASR_API int aii_asr_result(AiiAsrStream*, char* text, size_t capacity, char*, size_t);
AII_ASR_API int aii_asr_stats(AiiAsrStream*, AiiAsrStats*, char*, size_t);
// Separate addition; never enlarge the existing caller-allocated stats ABI.
AII_ASR_API int aii_asr_buffer_stats(AiiAsrStream*, AiiAsrBufferStats*, char*, size_t);
AII_ASR_API int aii_asr_stream_destroy(AiiAsrStream*, char*, size_t);
AII_ASR_API void aii_asr_cancel(AiiAsrStream*);
AII_ASR_API int aii_asr_busy(AiiAsrStream*);
// Atomic observation: 0 idle, 1 frontend, 2 encoder, 3 decoder, 4 joiner.
AII_ASR_API int aii_asr_phase(AiiAsrStream*);
AII_ASR_API const char* aii_asr_runtime_version(void);
#ifdef __cplusplus
}
#endif
