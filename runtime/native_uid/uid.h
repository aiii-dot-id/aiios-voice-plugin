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
AII_UID_API AiiUid* aii_uid_create(const void* model, size_t bytes,
    const char* backend, char* error, size_t error_capacity);
AII_UID_API AiiUid* aii_uid_create_ncnn(const void* graph, size_t graph_bytes,
    const void* weights, size_t weight_bytes, const char* backend,
    char* error, size_t error_capacity);
AII_UID_API int aii_uid_embed(AiiUid*, uint64_t utterance_id,
    const uint8_t* pcm, size_t bytes, int sample_rate, double* output,
    size_t output_dimensions, char* error, size_t error_capacity);
AII_UID_API int aii_uid_cancel_through(AiiUid*, uint64_t through);
AII_UID_API int aii_uid_phase(const AiiUid*);
AII_UID_API void aii_uid_destroy(AiiUid*);
#ifdef __cplusplus
}
#endif
