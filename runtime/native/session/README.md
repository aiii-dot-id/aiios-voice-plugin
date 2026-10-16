# Native session composition

Current lifecycle, output-only, settings and release-input contract:
[NATIVE_SESSION_CONTRACT.md](../../../docs/NATIVE_SESSION_CONTRACT.md).

The same C++17 session state machine builds on desktop and mobile. It joins
real native recognition, VAD, semantic endpoint, synthesis and speaker
identification on all three desktops, without an interpreter in the engine
process, behind its internal C interface. Its worker, connected to the Go
carrier and the Plugin SDK, is the resident engine of every released desktop
set since 0.1.0-beta.7. Which sets a release carries, their accelerator
placement and their qualification are recorded with that release; mobile is
not a released target.

## Ownership

Four workers own control/VAD, recognition, semantic inference and synthesis.
Input admission copies a bounded packet; it never calls a model. Control VAD
can fence every unrendered generation while recognition or synthesis is busy.
The renderer remains entirely host-owned. No device, browser, network, model
download or enrollment store is opened by this core.

The recognition owner retains 32 blocks of pre-roll and the existing semantic
gate's four provisional blocks. Pause settings are pinned before input and
computed in 16 kHz source samples, not transport batches. Finish admits an
exact future cutoff and waits a bounded time for the owed tail. Its partial
final block is padded for VAD only,
not counted as real recognition input. `input_finished` follows consumed input
and final transcript publication, including the silent case.

### Listening duration

`capture_limit_minutes` is a whole-number hearing setting, default **30**.
**0 disables automatic duration stopping.** Positive values count captured
audio, including silence, at the engine's 16 kHz clock; they do not count time
while no audio arrives. This is independent of the VAD turn pause and the
separate 30-second guided-enrollment recording bound. Changes are pinned at
the next session open and included in the existing effective settings readback.
The integer range is 0..4294967295, a representation bound, not a model or
hardware safety claim; sample conversion uses 64-bit arithmetic.

At a positive limit, the adapter splits a crossing packet at the exact cutoff,
finalizes the accepted input tail, and emits `input_finished` with
`reason: capture_limit` after the final transcript. `status.input_completion`
carries the same reason. Capture already in flight beyond the cutoff is not
transcribed; the host must stop capture on completion, finish pending answer
work and request normal drain. This is a visible completion, not an inference
fault or an automatic rollover. An explicitly requested earlier Finish still
wins. Stop, Cancel, Finish, Abort, bounded queues and missing-tail deadlines
remain effective when the duration limit is disabled.

The additive C entry `aii_voice_open_with_capture_limit` supplies the value;
existing entrypoints and struct layouts retain the 30-minute default. C callers
must split packets at a finite cutoff themselves. The core auto-finalizes at
that exact boundary. Tests use deterministic model doubles with more than
30 minutes of audio-clock input; this is not physical-audio or multi-day soak
qualification. Settled generations are now reclaimed, with at most 64 unresolved
jobs. Compact replay fences still grow with activation ID count; see the current
contract for the distinct memory bounds.

The worker's `--describe-settings` emits its compiled declaration without
loading models. Package builds replace `resources/settings.json` from
that output and rebind the runtime inventory. Unified desktop assembly reads
those bound declarations from all three archives and refuses disagreement or
the old seven-setting surface; it no longer inherits a stale parent template.

Synthesis output uses a 24 kHz clock. Generated, queued, delivered and rendered
are different quantities. An interruption removes queued PCM from every
unrendered generation and independently asks the backend to cancel. END is not
a playback receipt. Drain cannot retire the session until every generation's
terminal evidence is accepted; Abort is separately reported and does not claim
rendering. Inference faults remain faults even if cancellation was also asked.
Stop alone fences output without cancelling compute. Cancel remains available
after a stopped receipt; an early stopped receipt does not erase the need for
actual inference retirement and END consumption.

The Pocket native synthesizer requests a three-second initial PCM reserve per
reply. The core holds its first output until that reserve is generated, or
until a naturally shorter reply retires. The reserve fits below the existing
five-second bounded output queue. Other backends retain zero reserve. This
does not manufacture audio or change sample clocks: interruption fences held
PCM, and completion still requires the browser's actual render receipt. It
trades first-audio latency for protection against variable synthesis pauses.

The supplied model interfaces must outlive the session and have only one
active session. After `wait_closed` succeeds, the same resident models can be
reopened. Backend synthesis IDs remain monotonic across caller-ID restarts.
An outer supervisor still owns a force-kill deadline: the destructor never
frees an executing model merely to pretend a stuck kernel retired.

## Current executable boundary

- `session.h/.cpp`: transport-independent C++ API and bounded ownership.
- `native_models.cpp`: the native component adapters. Explicit qualified
  native libraries form the portable link contract; the retained Mac archive
  recipe remains available. A worker built with `AII_MULTITALKER_ASR`, as the
  released desktop sets are, takes its hearing composition from the sealed
  profile: the diarizer's model and device, the encoder's CUDA device and
  threads, and an optional Core ML or ONNX separator. Speech uses the backend
  it is given (`cpu`, `vulkan` or `metal`). Placement therefore differs by
  runtime set (see
  [component selection](../../../docs/RUNTIME_COMPONENT_SELECTION.md)), never
  by fallback. CPU remains the omitted-argument default.
- `session_test.cpp`: model-free contracts, including blocked inference,
  backpressure, stale output, receipt-held drain and packet-invariant pause.
- `probe.cpp`: recorded speech plus real native TTS, cancellation, complete
  recovery, Finish, simulated render receipts, Abort and model reuse.
- `text.h/.cpp`: the existing lossless UTF-8 partition contract, with a
  differential proof against the Python service's function.
- `continuous_probe.cpp`: more than one minute of input, more than two minutes
  of complete synthesis, second-segment cancellation and exact recovery.
- `CMakeLists.txt`: standalone contract build on all targets; the top-level
  common build also registers this owner and its tests.
- `c_api.h/.cpp`: common internal C boundary, bounded polling, explicit
  status/outcomes and model leases. A C caller must validate this ABI on its target.
- `native_c_api.cpp`: real model ownership behind that C boundary. Existing
  `aii_voice_models_load` retains its CPU ABI. The additive
  `aii_voice_models_load_with_backend` accepts `cpu`, `vulkan` or `metal`.
  Vulkan requires explicit process-local FP32. Linux desktop clears any
  device-zero pin and lets the runtime select a device; other Vulkan targets
  require device zero. Provider errors do not silently fall back to CPU.
- `worker.cpp`: carrier JSON/AUD1 adapter, with independent
  control/input readers and control/audio writers. It does not replace public
  SDK framing. The native executable sets offline telemetry policy before
  model initialization, then publishes readiness only after real warm inference.
- `worker_io.cpp`: bounded native pipes. Windows uses inherited handles and
  cancellation of the owning I/O thread, never Close behind synchronous I/O.
- `worker_test_models.cpp`: explicitly named fixture target only, never linked
  into the real engine. Readiness identifies it as a fixture.

Caller-owned asset verification remains mandatory. The proof harness binds
the existing graphs/checkpoint, VAD, Pocket assets and retained native libraries.
The engine does not accept a model selected by untrusted audio or transcript.
