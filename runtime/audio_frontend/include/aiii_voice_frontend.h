#ifndef AIII_VOICE_FRONTEND_H
#define AIII_VOICE_FRONTEND_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#if defined(_WIN32)
#if defined(AIII_VOICE_FRONTEND_STATIC)
#define VF_API
#elif defined(AIII_VOICE_FRONTEND_BUILD)
#define VF_API __declspec(dllexport)
#else
#define VF_API __declspec(dllimport)
#endif
#elif defined(__GNUC__) || defined(__clang__)
#define VF_API __attribute__((visibility("default")))
#else
#define VF_API
#endif

#define VF_FRONTEND_ABI_VERSION 1u

typedef struct vf_frontend vf_frontend;

typedef enum vf_status {
    VF_OK = 0,
    VF_INVALID_ARGUMENT = 1,
    VF_INVALID_MANIFEST = 2,
    VF_OUT_OF_MEMORY = 3,
    VF_OUTPUT_TOO_SMALL = 4,
    VF_ALREADY_FLUSHED = 5,
    VF_INTERNAL_ERROR = 6
} vf_status;

/* Returns the ABI version implemented by this library. */
VF_API uint32_t vf_frontend_abi_version(void);

/*
 * Creates a frontend from the exact immutable manifest bytes. The current
 * manifest grammar is specified in spec/PORTABLE_AUDIO_FRONTEND.md.
 */
VF_API vf_status vf_frontend_create(
    const uint8_t *manifest,
    size_t manifest_size,
    vf_frontend **out_frontend
);

/* Number of feature bins in every emitted frame. */
VF_API size_t vf_frontend_feature_bins(const vf_frontend *frontend);

/*
 * Exact frame capacity required by the next push/flush. These calls do not
 * mutate state. SIZE_MAX signals invalid arguments or arithmetic overflow.
 */
VF_API size_t vf_frontend_push_frame_capacity(
    const vf_frontend *frontend,
    size_t input_samples
);
VF_API size_t vf_frontend_flush_frame_capacity(const vf_frontend *frontend);

/*
 * Consumes interleaved PCM bytes in the manifest's declared representation.
 * output is row-major [frame][feature_bin]. A capacity failure consumes no
 * input and emits no frame.
 */
VF_API vf_status vf_frontend_push(
    vf_frontend *frontend,
    const uint8_t *pcm,
    size_t input_samples,
    float *output,
    size_t output_capacity_frames,
    size_t *out_frames
);

/* Emits all terminal right-padded frames. A second flush is refused. */
VF_API vf_status vf_frontend_flush(
    vf_frontend *frontend,
    float *output,
    size_t output_capacity_frames,
    size_t *out_frames
);

/* Restores byte-identical fresh-instance behavior under the same manifest. */
VF_API vf_status vf_frontend_reset(vf_frontend *frontend);

/* NULL is allowed. */
VF_API void vf_frontend_destroy(vf_frontend *frontend);

/* Stable, allocation-free diagnostics for status values. */
VF_API const char *vf_status_message(vf_status status);

#ifdef __cplusplus
}
#endif

#endif
