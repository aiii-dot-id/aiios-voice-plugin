#ifndef AII_VOICE_C_API_H
#define AII_VOICE_C_API_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct aii_voice_models aii_voice_models;
typedef struct aii_voice_session aii_voice_session;
typedef enum aii_voice_result {
  AII_VOICE_OK=0, AII_VOICE_AGAIN=1, AII_VOICE_CAPACITY=2,
  AII_VOICE_INVALID=3, AII_VOICE_BUSY=4, AII_VOICE_FAILED=5
} aii_voice_result;
typedef struct aii_voice_error { char message[512]; } aii_voice_error;
typedef struct aii_voice_paths {
  const char *asr, *mel, *vad, *endpoint, *coefficients, *pocket, *pocket_config;
} aii_voice_paths;
typedef struct aii_voice_settings {
  uint32_t pause_ms, input_tail_timeout_ms;
  float speech_threshold;
} aii_voice_settings;
typedef struct aii_voice_speech_settings {
  const char *voice, *tts_language, *stt_language;
  float temperature;
  uint32_t seed;
} aii_voice_speech_settings;
typedef struct aii_voice_open_options {
  const aii_voice_settings* control;
  const aii_voice_speech_settings* speech;
  uint32_t capture_limit_minutes;
  uint8_t input_enabled;
} aii_voice_open_options;
typedef struct aii_voice_snapshot {
  uint64_t received, controlled, recognized, generation, sequence, cutoff;
  uint64_t queued_audio_samples, synthesis_segments, completed_segments;
  uint64_t model_calls_ended;
  uint8_t input_finished, synthesizing, draining, stopping, retired, aborted;
  uint8_t closing, cutoff_set, recognition_active, model_call_in_flight;
  char error[512];
} aii_voice_snapshot;
typedef struct aii_voice_generation {
  uint64_t generated, delivered, rendered, queued;
  uint8_t fenced, cancelled, retired, end_taken, receipt, stopped;
} aii_voice_generation;
typedef struct aii_voice_event {
  uint64_t sequence, turn, generation, start, end;
  char kind[48];
  uint64_t evidence_start, evidence_end;
  uint8_t clean_track;
} aii_voice_event;
typedef struct aii_voice_audio {
  uint64_t generation, start;
  uint8_t end;
} aii_voice_audio;
typedef struct aii_voice_readiness {
  uint32_t models_loaded, probe_ms;
  char accelerator[32];
} aii_voice_readiness;
typedef struct aii_voice_capture {
  uint64_t samples;
  char embedding_binding[65], pcm_sha256[65];
  double embedding[256];
} aii_voice_capture;
typedef aii_voice_result (*aii_voice_track_observer)(void*, const aii_voice_capture*, char*, size_t, size_t*);
aii_voice_result aii_voice_models_track_observer(aii_voice_models*, aii_voice_track_observer, void*, aii_voice_error*);
typedef aii_voice_result (*aii_voice_track_observer_at)(void*, uint64_t, uint64_t, const aii_voice_capture*, char*, size_t, size_t*);
aii_voice_result aii_voice_models_track_observer_at(aii_voice_models*, aii_voice_track_observer_at, void*, aii_voice_error*);

aii_voice_result aii_voice_models_load(const aii_voice_paths*, aii_voice_models**, aii_voice_error*);
aii_voice_result aii_voice_models_load_with_backend(const aii_voice_paths*, const char* tts_backend, aii_voice_models**, aii_voice_error*);
typedef aii_voice_result (*aii_voice_snapshot_reader)(void* context, char* bytes, size_t capacity, size_t* written);
aii_voice_result aii_voice_models_load_with_uid(const aii_voice_paths*, const char* tts_backend,
  const char* uid_model, const char* policy_json, size_t policy_bytes,
  aii_voice_snapshot_reader, void* context, aii_voice_models**, aii_voice_error*);
aii_voice_result aii_voice_models_load_configured(const aii_voice_paths*, const char* tts_backend,
  const char* uid_model, const char* policy_json, size_t policy_bytes,
  aii_voice_snapshot_reader, void* context, const char* asr_execution_json,
  aii_voice_models**, aii_voice_error*);
aii_voice_result aii_voice_models_load_uid_policies(const aii_voice_paths*, const char* tts_backend,
  const char* uid_model, const char* policy_json, size_t policy_bytes,
  aii_voice_snapshot_reader, void* context, const char* asr_execution_json,
  const char* previous_policy_json, size_t previous_policy_bytes,
  aii_voice_models**, aii_voice_error*);
aii_voice_result aii_voice_models_execution(aii_voice_models*,char*,size_t,size_t*,aii_voice_error*);
aii_voice_result aii_voice_models_prefer(aii_voice_models*,const char* const* terms,uint32_t count,uint32_t* accepted,aii_voice_error*);
aii_voice_result aii_voice_models_warm(aii_voice_models*, aii_voice_readiness*, aii_voice_error*);
aii_voice_result aii_voice_models_warm_within(aii_voice_models*, uint32_t limit_ms, aii_voice_readiness*, aii_voice_error*);
aii_voice_result aii_voice_prepare_capture(aii_voice_models*, const float*, size_t,
  aii_voice_capture*, aii_voice_error*);
aii_voice_result aii_voice_models_release(aii_voice_models**, aii_voice_error*);
aii_voice_result aii_voice_open(aii_voice_models*, const aii_voice_settings*, aii_voice_session**, aii_voice_error*);
aii_voice_result aii_voice_open_session(aii_voice_models*, const aii_voice_open_options*, aii_voice_session**, aii_voice_error*);
typedef struct aii_voice_session_limits {
  uint32_t model_call_ms, input_tail_ms, output_take_ms;
  uint32_t endpoint_decision_ms, endpoint_retire_ms;
  uint32_t separation_min_ms, separation_max_ms;
} aii_voice_session_limits;
aii_voice_result aii_voice_open_bounded(aii_voice_models*, const aii_voice_open_options*, const aii_voice_session_limits*, aii_voice_session**, aii_voice_error*);
aii_voice_result aii_voice_open_configured(aii_voice_models*, const aii_voice_settings*, const aii_voice_speech_settings*, aii_voice_session**, aii_voice_error*);
aii_voice_result aii_voice_open_with_capture_limit(aii_voice_models*, const aii_voice_settings*, const aii_voice_speech_settings*, uint32_t capture_limit_minutes, aii_voice_session**, aii_voice_error*);
aii_voice_result aii_voice_release(aii_voice_session**, aii_voice_error*);
aii_voice_result aii_voice_feed(aii_voice_session*, uint64_t start, const float*, size_t count, aii_voice_error*);
aii_voice_result aii_voice_finish_input(aii_voice_session*, uint64_t end, aii_voice_error*);
aii_voice_result aii_voice_synthesize(aii_voice_session*, uint64_t generation, const char*, size_t bytes, aii_voice_error*);
aii_voice_result aii_voice_stop_playback(aii_voice_session*, uint64_t generation, aii_voice_error*);
aii_voice_result aii_voice_cancel_synthesis(aii_voice_session*, uint64_t generation, aii_voice_error*);
aii_voice_result aii_voice_release_generation(aii_voice_session*, uint64_t generation, aii_voice_error*);
typedef struct aii_voice_speech_state {
  char voice[65];
  float temperature;
  uint32_t seed;
  uint64_t revision;
  char refused[256];
} aii_voice_speech_state;
aii_voice_result aii_voice_speech_pending(aii_voice_session*, uint32_t wait_milliseconds, aii_voice_error*);
aii_voice_result aii_voice_set_speech(aii_voice_session*, const aii_voice_speech_settings*, aii_voice_error*);
aii_voice_result aii_voice_speech_unchanged(aii_voice_session*, aii_voice_error*);
aii_voice_result aii_voice_speech(aii_voice_session*, aii_voice_speech_state*, aii_voice_error*);
aii_voice_result aii_voice_playback(aii_voice_session*, uint64_t generation, uint64_t rendered, uint8_t terminal, uint8_t stopped, aii_voice_error*);
aii_voice_result aii_voice_close(aii_voice_session*, uint8_t abort, aii_voice_error*);
aii_voice_result aii_voice_wait(aii_voice_session*, uint32_t milliseconds, aii_voice_error*);
aii_voice_result aii_voice_status(aii_voice_session*, aii_voice_snapshot*, aii_voice_error*);
aii_voice_result aii_voice_enroll_selected(aii_voice_session*, const char* current,
  size_t current_bytes, const char* speaker_id, const char* label,
  const uint64_t* finals, size_t final_count, char* output, size_t capacity,
  size_t* required, aii_voice_error*);
aii_voice_result aii_voice_enrollment_finals(aii_voice_session*, uint64_t* finals,
  size_t capacity, size_t* required, aii_voice_error*);
aii_voice_result aii_voice_generation_status(aii_voice_session*, uint64_t generation, aii_voice_generation*, aii_voice_error*);
aii_voice_result aii_voice_next_event(aii_voice_session*, aii_voice_event*, char* text, size_t capacity, size_t* required_text, aii_voice_error*);
aii_voice_result aii_voice_next_event_with_reference(aii_voice_session*, aii_voice_event*, uint64_t* refers_to, char* text, size_t capacity, size_t* required_text, aii_voice_error*);
aii_voice_result aii_voice_next_event_with_track(aii_voice_session*, aii_voice_event*, uint64_t* refers_to, char* track, size_t track_capacity, char* text, size_t capacity, size_t* required_text, aii_voice_error*);
aii_voice_result aii_voice_next_audio(aii_voice_session*, aii_voice_audio*, float* pcm, size_t capacity, size_t* required_samples, aii_voice_error*);
#ifdef __cplusplus
}
#endif
#endif
