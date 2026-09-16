#ifndef AII_AUDIO_RING_H
#define AII_AUDIO_RING_H
#include <stdint.h>
#include <stddef.h>

// Single audio producer / single worker consumer. No allocation or locks in push.
#define VF_MAX_FRAMES 8192
#define VF_QUEUE_CAPACITY 64
typedef struct VFQueue VFQueue;
typedef struct {
    uint32_t count;
    uint32_t time_flags; // bit 0: host time valid; bit 1: sample time valid
    uint64_t host_time;
    double sample_time;
    float samples[VF_MAX_FRAMES];
} VFFrame;
VFQueue *vf_queue_create(void);
void vf_queue_destroy(VFQueue *q);
int vf_queue_push(VFQueue *q, const float *samples, uint32_t count,
                  uint64_t host_time, double sample_time, uint32_t time_flags);
int vf_queue_pop(VFQueue *q, VFFrame *frame);
const float *vf_frame_samples(const VFFrame *frame);
uint64_t vf_queue_drops(VFQueue *q);
uint64_t vf_queue_depth(VFQueue *q);
#endif
