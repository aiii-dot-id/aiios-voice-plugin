/* Owned PulseAudio endpoint. No inference or Python callback runs in this lane.
 * PCM clocks count client samples; server acknowledgments are not acoustic
 * proof. All callbacks and API mutations share the PulseAudio mainloop lock. */
#include <math.h>
#include <pulse/pulseaudio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define IN_CAP 32000u
#define OUT_CAP 48000u
enum { IDLE = 0, ACTIVE = 1, DRAINING = 2, STOPPING = 3 };
typedef struct {
  uint64_t epoch, captured, read, submitted, written, discarded, rejected;
  uint64_t input_ack_ns, output_ack_ns, capture_holes, underflows;
  uint64_t input_after_seal;
  uint32_t input_available, output_available, state, input_finished, failed;
  int32_t corked, drain_state;
  int64_t read_index, write_index, input_target;
} vf_status;
typedef struct {
  pa_threaded_mainloop *loop;
  pa_context *context;
  pa_stream *input, *output;
  pa_operation *drain;
  float in[IN_CAP], out[OUT_CAP];
  uint32_t in_head, in_count, out_head, out_count;
  int closing, finish_requested, output_corked;
  vf_status status;
} vf_host;

size_t vf_pulse_status_size(void) { return sizeof(vf_status); }

static uint64_t now_ns(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec;
}
static void fail_at(vf_host *h, uint32_t line) {
  if (!h->status.failed)
    h->status.failed = line;
}
#define fail(h) fail_at((h), __LINE__)
static void context_state(pa_context *c, void *p) {
  vf_host *h = p;
  if (!h->closing && !PA_CONTEXT_IS_GOOD(pa_context_get_state(c)))
    fail(h);
}
static void stream_state(pa_stream *s, void *p) {
  vf_host *h = p;
  if (!h->closing && !PA_STREAM_IS_GOOD(pa_stream_get_state(s)))
    fail(h);
}
static void capture(pa_stream *s, size_t requested, void *p) {
  (void)requested;
  vf_host *h = p;
  while (pa_stream_readable_size(s) > 0) {
    const void *data = NULL;
    size_t bytes = 0;
    if (pa_stream_peek(s, &data, &bytes) < 0) {
      fail(h);
      return;
    }
    if (!bytes)
      return;
    size_t count = bytes / sizeof(float);
    if (bytes % sizeof(float)) {
      fail(h);
      pa_stream_drop(s);
      return;
    }
    /* Half-close is an explicit CLIENT admission boundary, not a claim that
     * cork/timing acknowledgments drained the server or microphone. */
    if (h->finish_requested) {
      h->status.input_after_seal += count;
      if (pa_stream_drop(s) < 0)
        fail(h);
      continue;
    }
    if (count > IN_CAP - h->in_count) {
      fail(h);
      pa_stream_drop(s);
      return;
    }
    /* A server-reported hole is missing input, not manufactured silence. */
    if (!data) {
      h->status.capture_holes += count;
      fail(h);
      pa_stream_drop(s);
      return;
    }
    const float *f = data;
    for (size_t i = 0; i < count; i++) {
      if (!isfinite(f[i])) {
        fail(h);
        pa_stream_drop(s);
        return;
      }
      h->in[(h->in_head + h->in_count + i) % IN_CAP] = f[i];
    }
    h->in_count += (uint32_t)count;
    h->status.captured += count;
    if (pa_stream_drop(s) < 0) {
      fail(h);
      return;
    }
  }
}
static void drain_done(pa_stream *s, int success, void *p) {
  (void)s;
  vf_host *h = p;
  if (!success)
    fail(h);
  h->status.state = IDLE;
  h->status.output_ack_ns = now_ns();
  pa_operation *cork = pa_stream_cork(h->output, 1, NULL, NULL);
  if (cork)
    pa_operation_unref(cork);
  else
    fail(h);
  h->output_corked = 1;
  if (h->drain) {
    pa_operation_unref(h->drain);
    h->drain = NULL;
  }
}
static void pump(vf_host *h) {
  if (h->status.failed || h->status.state == IDLE ||
      h->status.state == STOPPING)
    return;
  size_t writable = pa_stream_writable_size(h->output);
  if (writable == (size_t)-1) {
    fail(h);
    return;
  }
  while (h->out_count && writable >= sizeof(float)) {
    size_t count = h->out_count, contiguous = OUT_CAP - h->out_head;
    if (count > contiguous)
      count = contiguous;
    if (count > writable / sizeof(float))
      count = writable / sizeof(float);
    if (pa_stream_write(h->output, h->out + h->out_head, count * sizeof(float),
                        NULL, 0, PA_SEEK_RELATIVE) < 0) {
      fail(h);
      return;
    }
    h->out_head = (h->out_head + (uint32_t)count) % OUT_CAP;
    h->out_count -= (uint32_t)count;
    h->status.written += count;
    writable -= count * sizeof(float);
  }
  if (h->status.written && h->output_corked) {
    pa_operation *uncork = pa_stream_cork(h->output, 0, NULL, NULL);
    if (uncork)
      pa_operation_unref(uncork);
    else
      fail(h);
    h->output_corked = 0;
  }
  if (!h->out_count && h->status.state == DRAINING && !h->drain) {
    /* Release a short final fragment even when it cannot fill prebuffer. */
    pa_operation *trigger = pa_stream_trigger(h->output, NULL, NULL);
    if (trigger)
      pa_operation_unref(trigger);
    else
      fail(h);
    h->drain = pa_stream_drain(h->output, drain_done, h);
    if (!h->drain)
      fail(h);
  }
}
static void playback(pa_stream *s, size_t bytes, void *p) {
  (void)s;
  (void)bytes;
  pump(p);
}
static void underflow(pa_stream *s, void *p) {
  (void)s;
  vf_host *h = p;
  if (h->status.state == ACTIVE && h->status.submitted)
    h->status.underflows++;
}
static void stop_done(pa_stream *s, int success, void *p) {
  (void)s;
  vf_host *h = p;
  if (!success)
    fail(h);
  h->status.state = IDLE;
  h->status.output_ack_ns = now_ns();
}
static void input_done(pa_stream *s, int success, void *p) {
  (void)s;
  vf_host *h = p;
  if (!success) {
    fail(h);
    return;
  }
  h->status.input_finished = 1;
  h->status.input_ack_ns = now_ns();
}
void vf_pulse_destroy(vf_host *h) {
  if (!h)
    return;
  if (h->loop) {
    pa_threaded_mainloop_lock(h->loop);
    h->closing = 1;
    if (h->drain) {
      pa_operation_cancel(h->drain);
      pa_operation_unref(h->drain);
      h->drain = NULL;
    }
    if (h->input) {
      pa_stream_set_read_callback(h->input, NULL, NULL);
      pa_stream_disconnect(h->input);
      pa_stream_unref(h->input);
    }
    if (h->output) {
      pa_stream_disconnect(h->output);
      pa_stream_unref(h->output);
    }
    if (h->context) {
      pa_context_disconnect(h->context);
      pa_context_unref(h->context);
    }
    pa_threaded_mainloop_unlock(h->loop);
    pa_threaded_mainloop_stop(h->loop);
    pa_threaded_mainloop_free(h->loop);
  }
  free(h);
}
vf_host *vf_pulse_create(const char *sink, const char *source) {
  if (!sink || !*sink || !source || !*source)
    return NULL;
  vf_host *h = calloc(1, sizeof(*h));
  if (!h)
    return NULL;
  h->status.input_target = -1;
  h->loop = pa_threaded_mainloop_new();
  if (!h->loop) {
    free(h);
    return NULL;
  }
  h->context = pa_context_new(pa_threaded_mainloop_get_api(h->loop),
                              "AII Voice VF102 isolated endpoint");
  if (!h->context) {
    vf_pulse_destroy(h);
    return NULL;
  }
  pa_context_set_state_callback(h->context, context_state, h);
  if (pa_context_connect(h->context, NULL, PA_CONTEXT_NOAUTOSPAWN, NULL) < 0 ||
      pa_threaded_mainloop_start(h->loop) < 0) {
    vf_pulse_destroy(h);
    return NULL;
  }
  uint64_t deadline = now_ns() + 5000000000ull;
  int connected = 0;
  while (now_ns() < deadline) {
    pa_threaded_mainloop_lock(h->loop);
    if (h->status.failed) {
      pa_threaded_mainloop_unlock(h->loop);
      break;
    }
    if (!connected && pa_context_get_state(h->context) == PA_CONTEXT_READY) {
      pa_sample_spec a = {PA_SAMPLE_FLOAT32LE, 16000, 1},
                     b = {PA_SAMPLE_FLOAT32LE, 24000, 1};
      h->input = pa_stream_new(h->context, "vf102 input", &a, NULL);
      h->output = pa_stream_new(h->context, "vf102 output", &b, NULL);
      if (!h->input || !h->output) {
        fail(h);
        pa_threaded_mainloop_unlock(h->loop);
        break;
      }
      pa_stream_set_state_callback(h->input, stream_state, h);
      pa_stream_set_state_callback(h->output, stream_state, h);
      pa_stream_set_read_callback(h->input, capture, h);
      pa_stream_set_write_callback(h->output, playback, h);
      pa_stream_set_underflow_callback(h->output, underflow, h);
      pa_buffer_attr in = {.maxlength = 16000 * 4,
                           .tlength = (uint32_t)-1,
                           .prebuf = (uint32_t)-1,
                           .minreq = (uint32_t)-1,
                           .fragsize = 160 * 4};
      pa_buffer_attr out = {.maxlength = 24000 * 4,
                            .tlength = 2400 * 4,
                            .prebuf = (uint32_t)-1,
                            .minreq = 240 * 4,
                            .fragsize = (uint32_t)-1};
      pa_stream_flags_t flags = PA_STREAM_DONT_MOVE | PA_STREAM_ADJUST_LATENCY |
                                PA_STREAM_AUTO_TIMING_UPDATE;
      if (pa_stream_connect_record(h->input, source, &in, flags) < 0 ||
          pa_stream_connect_playback(h->output, sink, &out,
                                     flags | PA_STREAM_START_CORKED, NULL,
                                     NULL) < 0)
        fail(h);
      connected = 1;
      h->output_corked = 1;
    }
    int ready = connected && pa_stream_get_state(h->input) == PA_STREAM_READY &&
                pa_stream_get_state(h->output) == PA_STREAM_READY;
    if (ready && (strcmp(pa_stream_get_device_name(h->input), source) ||
                  strcmp(pa_stream_get_device_name(h->output), sink))) {
      fail(h);
      ready = 0;
    }
    pa_threaded_mainloop_unlock(h->loop);
    if (ready)
      return h;
    struct timespec delay = {0, 1000000};
    nanosleep(&delay, NULL);
  }
  vf_pulse_destroy(h);
  return NULL;
}
int vf_pulse_status(vf_host *h, vf_status *out) {
  if (!h || !out)
    return -1;
  pa_threaded_mainloop_lock(h->loop);
  *out = h->status;
  out->input_available = h->in_count;
  out->output_available = OUT_CAP - h->out_count;
  out->corked = pa_stream_is_corked(h->output);
  out->drain_state = h->drain ? (int32_t)pa_operation_get_state(h->drain) : -1;
  const pa_timing_info *timing = pa_stream_get_timing_info(h->output);
  if (timing) {
    out->read_index = timing->read_index;
    out->write_index = timing->write_index;
  }
  pa_threaded_mainloop_unlock(h->loop);
  return 0;
}
int vf_pulse_begin(vf_host *h, uint64_t epoch) {
  if (!h || !epoch)
    return -1;
  pa_threaded_mainloop_lock(h->loop);
  int result = -1;
  if (!h->status.failed && h->status.state == IDLE && epoch > h->status.epoch) {
    h->status.epoch = epoch;
    h->status.state = ACTIVE;
    h->status.output_ack_ns = 0;
    h->status.submitted = 0;
    h->status.written = 0;
    h->status.underflows = 0;
    result = 0;
  }
  pa_threaded_mainloop_unlock(h->loop);
  return result;
}
int vf_pulse_write(vf_host *h, uint64_t epoch, const float *data,
                   uint32_t count) {
  if (!h || !data || !count || count > OUT_CAP)
    return -1;
  for (uint32_t i = 0; i < count; i++)
    if (!isfinite(data[i]) || fabsf(data[i]) > 1.001f)
      return -1;
  pa_threaded_mainloop_lock(h->loop);
  int result = 0;
  if (epoch != h->status.epoch || h->status.state != ACTIVE ||
      h->status.failed) {
    h->status.rejected += count;
    result = -2;
  } else if (count > OUT_CAP - h->out_count)
    result = -3;
  else {
    for (uint32_t i = 0; i < count; i++)
      h->out[(h->out_head + h->out_count + i) % OUT_CAP] = data[i];
    h->out_count += count;
    h->status.submitted += count;
    pump(h);
  }
  pa_threaded_mainloop_unlock(h->loop);
  return result;
}
int vf_pulse_read(vf_host *h, float *data, uint32_t capacity) {
  if (!h || !data || !capacity)
    return -1;
  pa_threaded_mainloop_lock(h->loop);
  uint32_t count = h->in_count;
  if (count > capacity)
    count = capacity;
  for (uint32_t i = 0; i < count; i++)
    data[i] = h->in[(h->in_head + i) % IN_CAP];
  h->in_head = (h->in_head + count) % IN_CAP;
  h->in_count -= count;
  h->status.read += count;
  int result = h->status.failed ? -1 : (int)count;
  pa_threaded_mainloop_unlock(h->loop);
  return result;
}
int vf_pulse_end(vf_host *h, uint64_t epoch) {
  if (!h)
    return -1;
  pa_threaded_mainloop_lock(h->loop);
  int result = -1;
  if (!h->status.failed && epoch == h->status.epoch &&
      h->status.state == ACTIVE && h->status.submitted) {
    h->status.state = DRAINING;
    pump(h);
    result = 0;
  }
  pa_threaded_mainloop_unlock(h->loop);
  return result;
}
int vf_pulse_stop(vf_host *h, uint64_t epoch) {
  if (!h)
    return -1;
  pa_threaded_mainloop_lock(h->loop);
  int result = -1;
  if (!h->status.failed && epoch == h->status.epoch &&
      (h->status.state == ACTIVE || h->status.state == DRAINING)) {
    h->status.state = STOPPING;
    h->status.discarded += h->out_count;
    h->out_count = 0;
    if (h->drain) {
      pa_operation_cancel(h->drain);
      pa_operation_unref(h->drain);
      h->drain = NULL;
    }
    pa_operation *cork = pa_stream_cork(h->output, 1, NULL, NULL);
    if (cork)
      pa_operation_unref(cork);
    else
      fail(h);
    h->output_corked = 1;
    pa_operation *op = pa_stream_flush(h->output, stop_done, h);
    if (op) {
      pa_operation_unref(op);
      result = 0;
    } else
      fail(h);
  }
  pa_threaded_mainloop_unlock(h->loop);
  return result;
}
int vf_pulse_finish_input(vf_host *h) {
  if (!h)
    return -1;
  pa_threaded_mainloop_lock(h->loop);
  int result = -1;
  if (!h->status.failed && !h->finish_requested) {
    /* Admit all currently delivered PCM under the same lock as capture/read.
     * The ring remains intact for the consumer to drain after this seal. */
    capture(h->input, 0, h);
    if (h->status.failed) {
      pa_threaded_mainloop_unlock(h->loop);
      return -1;
    }
    h->status.input_target = (int64_t)h->status.captured;
    h->finish_requested = 1;
    pa_operation *op = pa_stream_cork(h->input, 1, input_done, h);
    if (op) {
      pa_operation_unref(op);
      result = 0;
    } else
      fail(h);
  }
  pa_threaded_mainloop_unlock(h->loop);
  return result;
}
