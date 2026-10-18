#pragma once
#include <stddef.h>
#include <stdint.h>
#if defined(AII_ECAPA_STATIC)
#define AII_ECAPA_API
#elif defined(_WIN32)
#if defined(AII_ECAPA_BUILD)
#define AII_ECAPA_API __declspec(dllexport)
#else
#define AII_ECAPA_API __declspec(dllimport)
#endif
#else
#define AII_ECAPA_API __attribute__((visibility("default")))
#endif
#ifdef __cplusplus
extern "C" {
#endif
typedef int (*aii_ecapa_cancelled)(void *);
AII_ECAPA_API int aii_ecapa_fbank(const uint8_t *pcm, size_t bytes, int rate,
                                  float *output, size_t capacity,
                                  size_t *frames, aii_ecapa_cancelled cancelled,
                                  void *context);
#ifdef __cplusplus
}
#endif
