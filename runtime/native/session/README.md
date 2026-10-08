# Native session composition

Current lifecycle, output-only, settings and release-input contract:
[NATIVE_SESSION_CONTRACT.md](../../../docs/NATIVE_SESSION_CONTRACT.md).
The dated checkpoint measurements below are historical, not qualification of
an executable subsequently rebuilt from this source.

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
loading models. Candidate rebuilds replace `resources/settings.json` from
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
The Mac SDK-to-Chrome diagnostic found zero scheduled gaps in three Eponine
replies after this change, including one generated in 10.53 seconds for 15.28
seconds of audio; before it, a same-text run had 2.624 seconds of scheduled
silence. This is not an installed-host, physical-speaker, Windows or Ubuntu
quality verdict. Those remain separate release gates.

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
  status/outcomes and model leases. A C caller is tested on desktop and Pixel.
- `native_c_api.cpp`: real model ownership behind that C boundary. Existing
  `aii_voice_models_load` retains its CPU ABI. The additive
  `aii_voice_models_load_with_backend` accepts `cpu`, `vulkan` or `metal`.
  Vulkan requires explicit process-local FP32. Linux desktop clears any
  device-zero pin and lets the runtime select a device; other Vulkan targets
  require device zero. Provider errors do not silently fall back to CPU.
- `worker.cpp`: existing private carrier JSON/AUD1 adapter, with independent
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

## Historical September 12 checkpoint boundary

The internal C ABI and the eight existing speech controls now have a tested
native carrier adapter. Independent stop-playback/cancel-synthesis, future
Finish cutoff admission, status/readback, session-scoped events and render
receipts execute without a second public transport. The adapter accounts for
actual pipe writes separately from core dequeue. Natural receipt completion
requires transport END; stopped receipts cannot revive output. Settings are
bound to the opening session; delayed old replies cannot configure a successor.
EOF is ordered after already-admitted requests, and final response writes keep
their deadline watcher through retirement.

That checkpoint used explicit model paths and a smaller settings surface.
Current installed activation is zero-argument and runtime/model-bound; twenty
presets and the eight compiled settings are wired. Of those settings the
speaking voice, its variation and its seed are now taken at the first segment
of each reply, never inside one; the speaking language and the hearing
settings are still bound at session open. Unsupported values still
refuse explicitly. A new signed package needs its own installed qualification.

Load/open initialize on their own owner; they are not inference-free controls.
Release is externally exclusive with calls on the handle and refuses live
workers. The model-free static core and the linked real-model library have
different qualification boundaries.

Long input now retains the exact overlapping raw samples needed by the next
recurrent window, without resetting caches, tokens or the source clock. The
one-minute recognition refusal is removed; the session's explicit 30-minute
bound remains. This is rolling input storage, not a fabricated turn boundary.

Synthesis accepts up to 8,000 UTF-8 characters, partitions losslessly at the
existing 180-character default and retains one generation/clock/END for the
whole reply. Every segment of a reply uses the voice the reply began with.
Cancellation fences later
segments. The output queue has one shared 120,000-sample budget across all
generations, with a stalled-consumer deadline that its caller states
(`output_take_timeout_ms`; the worker states the limits table's
`output_take_ms`, 15 seconds by default). A pathological single segment still
faults at 60 seconds of audio; a whole reply is bounded at 30 minutes of it.

`capacity-history.patch` (`runtime/native_pocket`) chooses the current text's
canonical CPU shape and reuses a graph only when that shape matches; model
weights stay loaded. No shipped library is built with the whole of it: the
Linux library holds one of its two changes (a prepared graph is reused only at
exactly the capacity asked for) and not the other (the capacity chosen for a
graph planned on the processor); the Windows and macOS libraries hold neither.
A long prompt's larger cached graph therefore changes a later short reply's
PCM, by how much depending on the system and on where the graph is planned.
Measured as the difference between a short reply spoken after a long one and
the same reply in a fresh session, in two voices: on the Linux Small-CPU set,
where the engine plans on the processor, at most 4 units of 16-bit PCM, 85 dB
below the speech; on the Windows Small-CPU set, which plans there too, at most
45 units, 61 dB below the speech; through Vulkan on Linux, whose library holds
a change of its own for a graph not planned on the processor
(`runtime/native_pocket/engine_overrides_linux`), no difference; through
Vulkan on Windows at most 24 units, 67 dB below the speech; through Metal at
most 77 units, 58 dB below the speech. A reply that no longer reply precedes
is the fresh session's sample for sample on each.
Retained upstream sources and older checkpoint libraries are never edited.
This is tested history independence for the retained cases, not a claim of
bit-identical synthesis across accelerator implementations.

The real model
gate observes 66.24 seconds as one turn, 134.24 seconds of complete output,
and the frozen 4-second recovery after interruption. All render receipts are
simulated; this is not a new installed or physical-audio qualification.

The historical proof above predates native UID publication, guided enrollment,
the current settings map and the preset catalogue (twenty voices now). Those are now composed in
source, but their qualification remains bound to each tested artifact. Current
native recognition is English, and speech is synthesized in the seven languages
the settings declare; an older multilingual experiment does not expand the
shipping declaration. Prior Windows containment evidence cannot
automatically qualify a replacement runtime.

Per-platform accelerator placement, complete native-model numerical panels,
installed SDK/browser conversation, physical playback, mobile app linkage and
signed packages remain separate gates. No human-level release claim follows
from these component or control tests.
