#include "AudioRing.h"
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>

struct VFQueue {
    _Atomic uint64_t write_index;
    _Atomic uint64_t read_index;
    _Atomic uint64_t drops;
    VFFrame frames[VF_QUEUE_CAPACITY];
};
VFQueue *vf_queue_create(void) {
    VFQueue *q = calloc(1, sizeof(VFQueue));
    if (!q) return NULL;
    atomic_init(&q->write_index, 0);
    atomic_init(&q->read_index, 0);
    atomic_init(&q->drops, 0);
    if (!atomic_is_lock_free(&q->write_index) || !atomic_is_lock_free(&q->drops)) {
        free(q); return NULL;
    }
    return q;
}
void vf_queue_destroy(VFQueue *q) { free(q); }
int vf_queue_push(VFQueue *q, const float *samples, uint32_t count,
                  uint64_t host_time, double sample_time, uint32_t time_flags) {
    uint64_t w = atomic_load_explicit(&q->write_index, memory_order_relaxed);
    uint64_t r = atomic_load_explicit(&q->read_index, memory_order_acquire);
    if (!samples || !count || count > VF_MAX_FRAMES || w-r >= VF_QUEUE_CAPACITY) {
        atomic_fetch_add_explicit(&q->drops, 1, memory_order_relaxed); return 0;
    }
    VFFrame *f = &q->frames[w % VF_QUEUE_CAPACITY];
    f->count = count; f->host_time = host_time;
    f->sample_time = sample_time; f->time_flags = time_flags;
    memcpy(f->samples, samples, count * sizeof(float));
    atomic_store_explicit(&q->write_index, w+1, memory_order_release);
    return 1;
}
int vf_queue_pop(VFQueue *q, VFFrame *out) {
    uint64_t r = atomic_load_explicit(&q->read_index, memory_order_relaxed);
    uint64_t w = atomic_load_explicit(&q->write_index, memory_order_acquire);
    if (r == w) return 0;
    VFFrame *f = &q->frames[r % VF_QUEUE_CAPACITY];
    memcpy(out, f, offsetof(VFFrame, samples) + f->count * sizeof(float));
    atomic_store_explicit(&q->read_index, r+1, memory_order_release);
    return 1;
}
uint64_t vf_queue_drops(VFQueue *q) { return atomic_load(&q->drops); }
const float *vf_frame_samples(const VFFrame *frame) { return frame->samples; }
uint64_t vf_queue_depth(VFQueue *q) {
    uint64_t r = atomic_load_explicit(&q->read_index, memory_order_acquire);
    uint64_t w = atomic_load_explicit(&q->write_index, memory_order_acquire);
    return w-r;
}
