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
/* ECAPA/SpeechBrain feature math, not a speaker-admission policy.
 * Input: little-endian mono PCM16, 16 kHz, 1..480000 samples.
 * Output: row-major [1 + samples/160, 80], sentence-mean normalized.
 * No resampling, cropping, dither, pre-emphasis or device ownership.
 * Silence produces zero features; the caller owns usable-speech admission.
 * Returns 0 success, 1 invalid arguments/capacity, 3 cancellation, 4 failure.
 * On failure output remains untouched and *frames is zero (when non-null).
 * Independent concurrent calls are supported. Polls cancellation every frame.
 * Capacity is measured in floats. The callback must not throw; all buffers
 * must remain valid for the call, with frames disjoint from input and output.
 */
typedef int (*aii_ecapa_cancelled)(void *);
AII_ECAPA_API int aii_ecapa_fbank(const uint8_t *pcm, size_t bytes, int rate,
                                  float *output, size_t capacity,
                                  size_t *frames, aii_ecapa_cancelled cancelled,
                                  void *context);
#ifdef __cplusplus
}
#endif
