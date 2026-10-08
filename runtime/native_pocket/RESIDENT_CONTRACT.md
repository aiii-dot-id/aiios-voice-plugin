# Private native Pocket adapter

This is the engine's internal library binding, **not a Plugin SDK extension**.
Browser audio, receipts, session state, authorization, input completion and
forced retirement remain with the existing host/carrier/session owners.

This contract was written for the Python owner in `backend.py`. No released
desktop set since 0.1.0-beta.7 carries that owner or any Python: the shipped
owner is the C++ session worker (`Pocket` in
`runtime/native/session/native_models.cpp`), which calls the same library
through `nv_create_bound`, `nv_configure_voice`, `nv_start`, `nv_next`,
`nv_cancel`, `nv_reset`, `nv_destroy` and, on Linux, `nv_execution_info`. The
statements below that belong to the Python owner (its `tts_stream`, `tts_next`
and `cancel_synthesis` interface, `SpeechOutput`, the GIL, the lifetime lock,
the generation it assigns, the copy it takes and its 512-character input bound)
describe that harness only. The library's own rules in the ABI table hold for
either owner.

The adapter supplies the existing `tts_stream`, `tts_next`, `cancel_synthesis`
interface. `SpeechOutput` still owns bounded queues and the final delivery
fence. No function opens an audio device or a network connection.

## Ownership

One model-executor thread serializes native create/start/next/reset/destroy.
The binding refuses a concurrent owner instead of queueing it invisibly.
Native state and cancellation are atomic, do not acquire the inference lock,
and do not wait for compute. `ctypes.CDLL` releases Python's GIL during compute.
Do not use `PyDLL` for this library.

The Python lifetime lock serializes short control calls against final model
destruction. It is not held during native inference or preparation. Destroy
is allowed only after every stream/model call and control user has retired.
Failed retirement does not free a still-used handle or permit model reuse.

The Python owner assigns a monotonically increasing generation **before**
starting native preparation. Cancellation records that exact generation even
if it arrives before native start. An old generation cannot cancel a new one.
Cancelled audio is checked on both sides of the library return and again by
`SpeechOutput` before it can enter the outbound queue. The host's independent
playback fence still governs audio already delivered to the browser.

## Internal ABI

| Call | Owner and return meaning |
| --- | --- |
| `nv_create` | Model owner; loads verified assets and prepares a session; returns a handle or a bounded error. This is not an admission-only control. |
| `nv_create_bound` | Model owner; as `nv_create`, from a bound model root and its explicit configuration file, which is required. Backend is an explicit `cpu`, `vulkan` or `metal` with 1 to 4 threads; the default preset is bound. |
| `nv_create_voice_bound` | Model owner; as `nv_create_bound`, with the preset named at creation. |
| `nv_configure_voice` | Model owner, between generations; selects the preset and sampling temperature (0 to 1) for later generations. Validates before it changes anything, including that the preset's file is present; loads no model and runs no inference. Busy (-3) while a generation is active or computing. |
| `nv_execution_info` | Linux desktop builds only; copies the backend selection recorded at creation (requested backend and, for Vulkan, the chosen device). It takes no owner lock, so the caller keeps the handle alive across the call. It is not proof of hardware execution. |
| `nv_start` | Model owner; binds text, seed, noise and frame bound to the prepared request. May compute during preparation. |
| `nv_next` | Model owner; one <=1,920-sample mono 24 kHz frame, natural EOS, cancellation, or an error. |
| `nv_cancel` | Control thread; atomic generation fence only, returns immediately. |
| `nv_state` | Control thread; diagnostic atomic flags, not an authoritative public SDK snapshot. |
| `nv_reset` | Model owner after pending inference retires; resets per-stream state, preserves the loaded model. |
| `nv_destroy` | Model owner after all users retire; releases the handle. A busy handle is refused, never freed. |

Statuses: 1=audio, 0=success/natural end, -1=error, -2=cancelled,
-3=busy/refused owner. Caller-owned buffers never outlive their call. Audio
returned to Python is copied before the next native pull. C++ exceptions never
cross the ABI. A native model error remains an error even if cancellation races
it; cancellation cannot turn a broken model into a reusable successful one.

Inputs are explicitly bounded: Python <=512 NUL-free Unicode characters,
UTF-8 <=2,048 bytes natively; seed is uint32; max steps 1..750; an individual
segment cannot emit over 60 seconds. There is no silent max-step truncation:
the patched acoustic stream refuses reaching its bound before natural EOS and
its tail. Such model failure requires reload, not an invented completion.

Vulkan selection is explicit and process-local. The currently tested GTX 1070
path requires FP32 intermediates and device zero. CPU is a separate explicit
choice, not a hidden fallback. DLL, original model, tokenizer, voice and config
are hash-bound before loading; production packaging must bind the remaining
dependency file set as well.

## Evidence needed before promotion

Native component accuracy and reset tests do not prove this binding. Required
checks include real model calls through the library, cancellation while a call
is observed active, a response before that call completes, no stale PCM,
same-capacity exact recovery, natural-EOS refusal, shared SpeechOutput behavior,
and clean retirement. Full SDK/browser/UID and signed installation remain
additional required boundaries, not claims made by this internal contract.

The Windows resident/shared-service checks now pass, with retained
counterexamples and an independently verified archive. This changes the
component evidence, not its promotion status or the public SDK contract.
