#ifndef AIII_UID_FRONTEND_H
#define AIII_UID_FRONTEND_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef int (*aiii_uid_cancelled)(void *context);
int aiii_uid_fbank(const uint8_t *pcm, size_t bytes, int sample_rate,
                   float *output, size_t capacity, size_t *frames,
                   aiii_uid_cancelled cancelled, void *context);
#ifdef __cplusplus
}
#endif
#endif
