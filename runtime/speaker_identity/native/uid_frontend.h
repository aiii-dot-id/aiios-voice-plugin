#ifndef AIII_UID_FRONTEND_H
#define AIII_UID_FRONTEND_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Model-specific, final-utterance frontend. Not the generic STT frontend.
 * 16 kHz mono signed PCM16 little endian, 31,920..480,000 samples.
 * Features: row-major [frames,80], original Kaldi snip-edges + utterance CMN.
 * No padding, cropping, resampling, speaker database or device ownership.
 * Call on a bounded worker; cancelled may be NULL and is polled per frame.
 * On any error the caller's output remains untouched, *frames becomes zero.
 * Returns 0 success, 1 arguments/bounds, 2 insufficient usable signal,
 * 3 cancelled, 4 computation error. No C++ exception crosses this boundary.
 */
typedef int (*aiii_uid_cancelled)(void *context);
int aiii_uid_fbank(const uint8_t *pcm, size_t bytes, int sample_rate,
                   float *output, size_t capacity, size_t *frames,
                   aiii_uid_cancelled cancelled, void *context);
#ifdef __cplusplus
}
#endif
#endif
