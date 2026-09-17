#pragma once
#include <stddef.h>
#include <stdint.h>
#if defined(_WIN32)
#if defined(AII_UID_BUILD)
#define AII_UID_API __declspec(dllexport)
#else
#define AII_UID_API __declspec(dllimport)
#endif
#else
#define AII_UID_API
#endif
#ifdef __cplusplus
extern "C" {
#endif
typedef struct AiiUid AiiUid;
/* Private native-engine ABI, not an SDK extension. The asset owner first
 * verifies exact model bytes and the policy binding from model_contract.h.
 * This owner retains model bytes. Explicit cpu or cuda; never backend fallback.
 * ORT_DISABLE_TELEMETRY=1 must precede creation. No network or file discovery.
 */
AII_UID_API AiiUid* aii_uid_create(const void* model, size_t bytes,
    const char* backend, char* error, size_t error_capacity);
/* Private native representation of the SAME bound embedding space. Caller
 * verifies both exact files against model_contract.h before creation. Both
 * buffers are retained by copy. Explicit ncnn-cpu reference or ncnn-vulkan;
 * unavailable GPU is a refusal. ORT builds refuse this representation. */
AII_UID_API AiiUid* aii_uid_create_ncnn(const void* graph, size_t graph_bytes,
    const void* weights, size_t weight_bytes, const char* backend,
    char* error, size_t error_capacity);
/* One embedding worker. Input is complete, immutable PCM16 little endian,
 * mono 16 kHz, 31920..480000 samples (1.995..30 seconds), never cropped/padded.
 * IDs are positive and strictly increasing within this owner. Output is 256
 * float64 unit-vector values, untouched on refusal, cancellation or failure.
 * 0 success; 1 invalid/unsupported; 2 unusable signal; 3 cancelled; 4 fault;
 * 5 another embed is active. No exception crosses this ABI.
 */
AII_UID_API int aii_uid_embed(AiiUid*, uint64_t utterance_id,
    const uint8_t* pcm, size_t bytes, int sample_rate, double* output,
    size_t output_dimensions, char* error, size_t error_capacity);
/* Control-thread safe: fence IDs <= through, idempotent and monotonic.
 * Never waits on frontend/model inference. Also requests ORT termination;
 * an already-running kernel retires on its own worker. No stale vector can
 * publish after this fence returns. Kernel retirement is measured separately.
 */
AII_UID_API int aii_uid_cancel_through(AiiUid*, uint64_t through);
/* Atomic observation: 0 idle, 1 frontend, 2 inference. Observation is not an
 * inference-completion or GPU-placement receipt. */
AII_UID_API int aii_uid_phase(const AiiUid*);
/* Destroy only after all embed/control callers retire. */
AII_UID_API void aii_uid_destroy(AiiUid*);
#ifdef __cplusplus
}
#endif
