#ifndef AII_NATIVE_ECHO_H
#define AII_NATIVE_ECHO_H
#include <stddef.h>
#include <stdint.h>
#if defined(_WIN32)
#define AII_ECHO_API __declspec(dllexport)
#else
#define AII_ECHO_API __attribute__((visibility("default")))
#endif
#ifdef __cplusplus
extern "C" {
#endif
/* Serialized caller ownership; 16 kHz normalized mono, 160-sample frames.
 * This is an in-process DSP ABI, not an SDK or browser wire extension. */
typedef struct aii_echo aii_echo;
enum { AII_ECHO_OK = 0, AII_ECHO_INVALID = 1, AII_ECHO_FAILURE = 2 };
enum { AII_ECHO_REFERENCE_VALID = 1 };
enum {
  AII_ECHO_REFERENCE_MISSING = 0,
  AII_ECHO_PASSTHROUGH = 1,
  AII_ECHO_PROCESSING = 2
};
typedef struct {
  uint64_t generation, next_sample, processed_frames, missing_reference_frames;
  int state;
  int finished;
} aii_echo_status;
AII_ECHO_API int aii_echo_create(uint64_t generation, aii_echo **out);
/* A strictly newer route generation starts at sample zero with fresh state. */
AII_ECHO_API int aii_echo_reset(aii_echo *, uint64_t generation);
/* Invalid input leaves timeline/output unchanged. A missing reference is
 * explicitly reported, passes microphone unchanged, and resets adaptation.
 * Output is delayed one 160-sample frame, including bypass, so AEC's internal
 * block latency never drops/repeats microphone samples at mode transitions.
 * First process returns output_samples=0, subsequent calls return 160.
 * Finish drains the last real frame; padding is never returned as input.
 * A final short frame of 1..159 samples is allowed, followed only by Finish
 * or Reset. Output storage always has capacity for 160 samples. Finish returns
 * exactly the real short tail, and the input clock excludes internal padding.
 * Input/output arrays must not overlap each other. Render silence is a VALID
 * all-zero frame. The pair shares the capture clock; not TTS generation time.
 */
AII_ECHO_API int aii_echo_process(aii_echo *, uint64_t generation,
                                  uint64_t start_sample, const float *capture,
                                  const float *render, size_t samples,
                                  unsigned flags, float *output,
                                  size_t *output_samples);
AII_ECHO_API int aii_echo_finish(aii_echo *, uint64_t generation, float *output,
                                 size_t *output_samples);
AII_ECHO_API int aii_echo_get_status(const aii_echo *, aii_echo_status *);
AII_ECHO_API void aii_echo_destroy(aii_echo *);
#ifdef __cplusplus
}
#endif
#endif
