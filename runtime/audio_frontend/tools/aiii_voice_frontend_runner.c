#include "aiii_voice_frontend.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

static int read_all(const char *path, uint8_t **out, size_t *out_size) {
    FILE *file;
    long end;
    uint8_t *bytes;
    *out = NULL;
    *out_size = 0u;
    file = fopen(path, "rb");
    if (file == NULL || fseek(file, 0L, SEEK_END) != 0) {
        if (file != NULL) {
            fclose(file);
        }
        return 0;
    }
    end = ftell(file);
    if (end < 0L || fseek(file, 0L, SEEK_SET) != 0) {
        fclose(file);
        return 0;
    }
    if ((unsigned long)end > SIZE_MAX) {
        fclose(file);
        return 0;
    }
    bytes = (uint8_t *)malloc(end == 0L ? 1u : (size_t)end);
    if (bytes == NULL ||
        ((size_t)end != 0u && fread(bytes, 1u, (size_t)end, file) != (size_t)end) ||
        fclose(file) != 0) {
        free(bytes);
        return 0;
    }
    *out = bytes;
    *out_size = (size_t)end;
    return 1;
}

static int write_frames(
    FILE *file,
    const float *frames,
    size_t frame_count,
    size_t feature_bins
) {
    size_t values;
    if (frame_count > SIZE_MAX / feature_bins) {
        return 0;
    }
    values = frame_count * feature_bins;
    return values == 0u || fwrite(frames, sizeof(float), values, file) == values;
}

static float *allocate_frames(size_t frames, size_t feature_bins) {
    size_t values;
    if (frames == 0u) {
        return NULL;
    }
    if (feature_bins == 0u || frames > SIZE_MAX / feature_bins) {
        return NULL;
    }
    values = frames * feature_bins;
    if (values > SIZE_MAX / sizeof(float)) {
        return NULL;
    }
    return (float *)malloc(values * sizeof(float));
}

static int fail_status(const char *operation, vf_status status) {
    fprintf(stderr, "%s failed: %s (%d)\n", operation, vf_status_message(status), status);
    return 1;
}

int main(int argc, char **argv) {
    uint8_t *manifest = NULL;
    size_t manifest_size = 0u;
    uint8_t *pcm = NULL;
    size_t pcm_size = 0u;
    size_t input_samples;
    vf_frontend *frontend = NULL;
    vf_status status;
    size_t bins;
    size_t push_capacity;
    size_t push_frames = 0u;
    size_t flush_capacity;
    size_t flush_frames = 0u;
    float *output = NULL;
    FILE *output_file = NULL;
    const uint16_t endian_probe = 1u;
    int result = 1;

    if (argc != 4) {
        fprintf(stderr, "usage: %s MANIFEST PCM_S16LE OUTPUT_F32LE\n", argv[0]);
        return 2;
    }
    if (*(const uint8_t *)&endian_probe != 1u) {
        fprintf(stderr, "little-endian target required\n");
        return 1;
    }
    if (!read_all(argv[1], &manifest, &manifest_size) ||
        !read_all(argv[2], &pcm, &pcm_size) || pcm_size % 2u != 0u) {
        fprintf(stderr, "cannot read exact manifest or PCM input\n");
        goto cleanup;
    }
    input_samples = pcm_size / 2u;
    status = vf_frontend_create(manifest, manifest_size, &frontend);
    if (status != VF_OK) {
        result = fail_status("create", status);
        goto cleanup;
    }
    bins = vf_frontend_feature_bins(frontend);
    push_capacity = vf_frontend_push_frame_capacity(frontend, input_samples);
    if (push_capacity == SIZE_MAX) {
        fprintf(stderr, "push capacity overflow\n");
        goto cleanup;
    }
    output = allocate_frames(push_capacity, bins);
    if (push_capacity != 0u && output == NULL) {
        fprintf(stderr, "cannot allocate push output\n");
        goto cleanup;
    }
    output_file = fopen(argv[3], "wb");
    if (output_file == NULL) {
        fprintf(stderr, "cannot open output\n");
        goto cleanup;
    }
    status = vf_frontend_push(
        frontend,
        pcm,
        input_samples,
        output,
        push_capacity,
        &push_frames
    );
    if (status != VF_OK) {
        result = fail_status("push", status);
        goto cleanup;
    }
    if (push_frames != push_capacity ||
        !write_frames(output_file, output, push_frames, bins)) {
        fprintf(stderr, "push output contract failed\n");
        goto cleanup;
    }
    free(output);
    output = NULL;

    flush_capacity = vf_frontend_flush_frame_capacity(frontend);
    if (flush_capacity == SIZE_MAX) {
        fprintf(stderr, "flush capacity overflow\n");
        goto cleanup;
    }
    output = allocate_frames(flush_capacity, bins);
    if (flush_capacity != 0u && output == NULL) {
        fprintf(stderr, "cannot allocate flush output\n");
        goto cleanup;
    }
    status = vf_frontend_flush(
        frontend, output, flush_capacity, &flush_frames
    );
    if (status != VF_OK) {
        result = fail_status("flush", status);
        goto cleanup;
    }
    if (flush_frames != flush_capacity ||
        !write_frames(output_file, output, flush_frames, bins) ||
        fclose(output_file) != 0) {
        output_file = NULL;
        fprintf(stderr, "flush output contract failed\n");
        goto cleanup;
    }
    output_file = NULL;
    printf(
        "{\"abi_version\":%u,\"feature_bins\":%zu,\"frames\":%zu,"
        "\"input_samples\":%zu}\n",
        vf_frontend_abi_version(),
        bins,
        push_frames + flush_frames,
        input_samples
    );
    result = 0;

cleanup:
    if (output_file != NULL) {
        fclose(output_file);
    }
    free(output);
    vf_frontend_destroy(frontend);
    free(pcm);
    free(manifest);
    return result;
}
