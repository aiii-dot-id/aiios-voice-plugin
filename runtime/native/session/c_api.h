#ifndef AII_VOICE_C_API_H
#define AII_VOICE_C_API_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Internal embedding ABI, not a second public Plugin SDK transport. All strings
 * are UTF-8. Rates are fixed explicitly: mono float32 16000 input / 24000 output.
 * No device, download, enrollment store, or implicit accelerator fallback.
 * Different control/poll calls may run concurrently. Release must be externally
 * exclusive with all calls on that handle. No exception crosses this boundary. */
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
/* Canonical open options. Existing structs and entrypoint layouts are unchanged.
 * input_enabled is a strict boolean. Null setting pointers select defaults;
 * capture_limit_minutes=0 removes the duration stop, not resource bounds.
 * Options and strings need live only for the duration of open. */
typedef struct aii_voice_open_options {
  const aii_voice_settings* control;
  const aii_voice_speech_settings* speech;
  uint32_t capture_limit_minutes;
  uint8_t input_enabled;
} aii_voice_open_options;
typedef struct aii_voice_snapshot {
  uint64_t received, controlled, recognized, generation, sequence, cutoff;
  uint64_t queued_audio_samples, synthesis_segments, completed_segments;
  uint8_t input_finished, synthesizing, draining, stopping, retired, aborted;
  uint8_t closing, cutoff_set, recognition_active;
  char error[512];
} aii_voice_snapshot;
typedef struct aii_voice_generation {
  uint64_t generated, delivered, rendered, queued;
  uint8_t fenced, cancelled, retired, end_taken, receipt, stopped;
} aii_voice_generation;
typedef struct aii_voice_event {
  uint64_t sequence, turn, generation, start, end;
  char kind[48];
} aii_voice_event;
typedef struct aii_voice_audio {
  uint64_t generation, start;
  uint8_t end;
} aii_voice_audio;
typedef struct aii_voice_readiness {
  uint32_t models_loaded, probe_ms;
  char accelerator[32];
} aii_voice_readiness;
/* Private capture evidence, never an AI tool result or a permission token.
 * The caller owns consent, immutable recording custody, persistence and later
 * operator-confirmed enrollment. No raw audio is retained by this value. */
typedef struct aii_voice_capture {
  uint64_t samples;
  char embedding_binding[65], pcm_sha256[65];
  double embedding[256];
} aii_voice_capture;
/* Private model-composition callback, not a Plugin SDK operation. Runs only
 * on the bounded speaker worker. Null evidence means a provisional track.
 * The owner publishes via host CAS/readback before returning a UUID. Callback
 * and context outlive models; cancellation must wake any host wait. */
typedef aii_voice_result (*aii_voice_track_observer)(void*, const aii_voice_capture*, char*, size_t, size_t*);
aii_voice_result aii_voice_models_track_observer(aii_voice_models*, aii_voice_track_observer, void*, aii_voice_error*);

/* Cold model load and session open may block for initialization; they belong on
 * an initialization owner, never the ordered SDK admission/reader goroutine.
 * Load is supplied by the linked native model adapter, not the model-free core.
 * Every asset must already have been hash/inventory verified by the caller. */
aii_voice_result aii_voice_models_load(const aii_voice_paths*, aii_voice_models**, aii_voice_error*);
/* Same ownership contract, with explicit TTS execution selection. Only "cpu"
 * and "vulkan" are accepted; failure never retries on a different backend.
 * The original load entry retains its CPU behavior and binary ABI. */
aii_voice_result aii_voice_models_load_with_backend(const aii_voice_paths*, const char* tts_backend, aii_voice_models**, aii_voice_error*);
/* Optional UID composition. Snapshot callback runs on the UID worker, must be
 * bounded/cancellable by its owner, and writes canonical existing-format bytes.
 * Failure is unavailable, NEVER empty enrollments. Context outlives models.
 * This entry is supplied only by a runtime explicitly linked with native UID. */
typedef aii_voice_result (*aii_voice_snapshot_reader)(void* context, char* bytes, size_t capacity, size_t* written);
aii_voice_result aii_voice_models_load_with_uid(const aii_voice_paths*, const char* tts_backend,
  const char* uid_model, const char* policy_json, size_t policy_bytes,
  aii_voice_snapshot_reader, void* context, aii_voice_models**, aii_voice_error*);
/* Explicit ASR initialization JSON; null retains the prior target profile.
 * This does not change the layout or default behavior of existing C callers. */
aii_voice_result aii_voice_models_load_configured(const aii_voice_paths*, const char* tts_backend,
  const char* uid_model, const char* policy_json, size_t policy_bytes,
  aii_voice_snapshot_reader, void* context, const char* asr_execution_json,
  aii_voice_models**, aii_voice_error*);
/* Optional second immutable UID policy asset for explicit profile upgrades.
 * Existing profiles remain classified under their own bound policy until a
 * confirmed CAS changes it. Neither loading nor identification writes profiles.
 * Null/zero retains the single-policy behavior. Existing entrypoints are unchanged. */
aii_voice_result aii_voice_models_load_uid_policies(const aii_voice_paths*, const char* tts_backend,
  const char* uid_model, const char* policy_json, size_t policy_bytes,
  aii_voice_snapshot_reader, void* context, const char* asr_execution_json,
  const char* previous_policy_json, size_t previous_policy_bytes,
  aii_voice_models**, aii_voice_error*);
/* Immutable configuration readback; required includes NUL, never truncated.
 * Registration/selection is NOT a hardware-kernel placement attestation. */
aii_voice_result aii_voice_models_execution(aii_voice_models*,char*,size_t,size_t*,aii_voice_error*);
/* Explicit blocking warm inference, before public readiness and without an
 * active session. Does not capture audio or claim acoustic/quality evidence. */
aii_voice_result aii_voice_models_warm(aii_voice_models*, aii_voice_readiness*, aii_voice_error*);
/* Blocking background preparation from a completed, explicitly requested
 * recording. Requires no open microphone and shares the existing exclusive
 * model lease: BUSY while speech/warm/preparation owns it. 31920..480000 mono
 * float32 samples at exactly 16 kHz. Invalid/unusable input is refused, never
 * cropped or padded. Output is untouched unless OK. No profile read or write.
 * The composition owner must fence a late result after cancellation, and must
 * not free models/input/output until this call has retired. */
aii_voice_result aii_voice_prepare_capture(aii_voice_models*, const float*, size_t,
  aii_voice_capture*, aii_voice_error*);
aii_voice_result aii_voice_models_release(aii_voice_models**, aii_voice_error*);
aii_voice_result aii_voice_open(aii_voice_models*, const aii_voice_settings*, aii_voice_session**, aii_voice_error*);
aii_voice_result aii_voice_open_session(aii_voice_models*, const aii_voice_open_options*, aii_voice_session**, aii_voice_error*);
/* Settings are pinned for the session. Unknown languages/voices are refused;
 * open is initialization work and never runs on the interruption lane. */
aii_voice_result aii_voice_open_configured(aii_voice_models*, const aii_voice_settings*, const aii_voice_speech_settings*, aii_voice_session**, aii_voice_error*);
/* Additive ABI: prior entrypoints retain the 30-minute default and their
 * struct layouts. 0 disables duration stopping, not queue/cancellation bounds.
 * Input packets must not cross the finite limit; at the exact limit the core
 * finalizes input and emits input_finished with reason capture_limit. */
aii_voice_result aii_voice_open_with_capture_limit(aii_voice_models*, const aii_voice_settings*, const aii_voice_speech_settings*, uint32_t capture_limit_minutes, aii_voice_session**, aii_voice_error*);
/* Release refuses a live owner. Abort is admission-only; wait is explicitly
 * separate. Never free an executing kernel. The outer supervisor owns kill. */
aii_voice_result aii_voice_release(aii_voice_session**, aii_voice_error*);
aii_voice_result aii_voice_feed(aii_voice_session*, uint64_t start, const float*, size_t count, aii_voice_error*);
aii_voice_result aii_voice_finish_input(aii_voice_session*, uint64_t end, aii_voice_error*);
aii_voice_result aii_voice_synthesize(aii_voice_session*, uint64_t generation, const char*, size_t bytes, aii_voice_error*);
aii_voice_result aii_voice_stop_playback(aii_voice_session*, uint64_t generation, aii_voice_error*);
aii_voice_result aii_voice_cancel_synthesis(aii_voice_session*, uint64_t generation, aii_voice_error*);
aii_voice_result aii_voice_release_generation(aii_voice_session*, uint64_t generation, aii_voice_error*);
aii_voice_result aii_voice_playback(aii_voice_session*, uint64_t generation, uint64_t rendered, uint8_t terminal, uint8_t stopped, aii_voice_error*);
aii_voice_result aii_voice_close(aii_voice_session*, uint8_t abort, aii_voice_error*);
aii_voice_result aii_voice_wait(aii_voice_session*, uint32_t milliseconds, aii_voice_error*);
aii_voice_result aii_voice_status(aii_voice_session*, aii_voice_snapshot*, aii_voice_error*);
/* Pure candidate preparation from retained finalized spans. No inference, host
 * call, authorization or publication. current is canonical, validated host
 * bytes (never missing/error-as-empty). Caller owns operator selection and
 * session fencing, pins this handle until return, and CAS-publishes separately.
 * CAPACITY reports required bytes, including NUL; no side effects on retry. */
aii_voice_result aii_voice_enroll_selected(aii_voice_session*, const char* current,
  size_t current_bytes, const char* speaker_id, const char* label,
  const uint64_t* finals, size_t final_count, char* output, size_t capacity,
  size_t* required, aii_voice_error*);
/* Discovery only: final sequence IDs, never vectors/audio. Expiry and session
 * liveness are checked again on preparation; listing reserves no evidence. */
aii_voice_result aii_voice_enrollment_finals(aii_voice_session*, uint64_t* finals,
  size_t capacity, size_t* required, aii_voice_error*);
aii_voice_result aii_voice_generation_status(aii_voice_session*, uint64_t generation, aii_voice_generation*, aii_voice_error*);
/* AGAIN = empty queue; CAPACITY leaves the item in core custody. required_text
 * includes NUL; required_samples is float count. Retry after resize: intervening
 * control may retire the PCM, so never assume the same item is still present.
 * OK audio END can have required_samples == 0. Dequeue is NOT physical render. */
aii_voice_result aii_voice_next_event(aii_voice_session*, aii_voice_event*, char* text, size_t capacity, size_t* required_text, aii_voice_error*);
/* Additive ABI: original event struct and entry retain their binary layout. */
aii_voice_result aii_voice_next_event_with_reference(aii_voice_session*, aii_voice_event*, uint64_t* refers_to, char* text, size_t capacity, size_t* required_text, aii_voice_error*);
/* Additive acoustic-track readout. track must provide 64 bytes; the existing
 * event layout and legacy entrypoints remain unchanged. */
aii_voice_result aii_voice_next_event_with_track(aii_voice_session*, aii_voice_event*, uint64_t* refers_to, char* track, size_t track_capacity, char* text, size_t capacity, size_t* required_text, aii_voice_error*);
aii_voice_result aii_voice_next_audio(aii_voice_session*, aii_voice_audio*, float* pcm, size_t capacity, size_t* required_samples, aii_voice_error*);
#ifdef __cplusplus
}
#endif
#endif
