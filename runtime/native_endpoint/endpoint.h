#pragma once
#include <stddef.h>
#include <stdint.h>
#if defined(_WIN32)
#if defined(AII_ENDPOINT_BUILD)
#define AII_ENDPOINT_API __declspec(dllexport)
#else
#define AII_ENDPOINT_API __declspec(dllimport)
#endif
#else
#define AII_ENDPOINT_API
#endif
#ifdef __cplusplus
extern "C" {
#endif
typedef struct AiiEndpoint AiiEndpoint;
// Private engine ABI. Caller supplies hash-verified model and coefficients;
// CPU control inference only. Requires ORT_DISABLE_TELEMETRY=1 before create.
AII_ENDPOINT_API AiiEndpoint* aii_endpoint_create(const void* model, size_t bytes,
    const float* coefficients, size_t count, char* error, size_t error_capacity);
// One worker; bounded finite mono 16 kHz float32. Last eight seconds retained,
// normalized after left padding, no endpoint threshold decision in this API.
// Positive monotonic IDs. 0 success, 1 invalid, 3 cancelled, 4 fault, 5 busy.
// Probability and optional [1,80,800] feature output untouched unless success.
AII_ENDPOINT_API int aii_endpoint_score(AiiEndpoint*, uint64_t query_id,
    const float* pcm, size_t samples, double* probability,
    float* feature_output, size_t feature_capacity, char* error, size_t error_capacity);
AII_ENDPOINT_API int aii_endpoint_cancel_through(AiiEndpoint*, uint64_t query_id);
AII_ENDPOINT_API int aii_endpoint_phase(const AiiEndpoint*);
// Caller retires every worker/control before destroying.
AII_ENDPOINT_API void aii_endpoint_destroy(AiiEndpoint*);
#ifdef __cplusplus
}
#endif
