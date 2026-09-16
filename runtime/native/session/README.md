# Native session composition

The same C++17 session state machine builds on desktop and mobile. It now
joins real native recognition, VAD, semantic endpoint and synthesis on all three desktops,
without an interpreter in the engine process. Its internal C interface is now
implemented and exercised with those models. This is a private composition
milestone, **not yet the complete shipping resident**. Its native private worker
now connects to the existing Go carrier/Plugin SDK and passes real Mac CPU and
Windows and Ubuntu CPU/Vulkan speech. The independent three-platform gate is
`deliverables/native-desktop-linux-20260912-r1/README.md`.

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

Synthesis output uses a 24 kHz clock. Generated, queued, delivered and rendered
are different quantities. An interruption removes queued PCM from every
unrendered generation and independently asks the backend to cancel. END is not
a playback receipt. Drain cannot retire the session until every generation's
terminal evidence is accepted; Abort is separately reported and does not claim
rendering. Inference faults remain faults even if cancellation was also asked.
Stop alone fences output without cancelling compute. Cancel remains available
after a stopped receipt; an early stopped receipt does not erase the need for
actual inference retirement and END consumption.

The supplied model interfaces must outlive the session and have only one
active session. After `wait_closed` succeeds, the same resident models can be
reopened. Backend synthesis IDs remain monotonic across caller-ID restarts.
An outer supervisor still owns a force-kill deadline: the destructor never
frees an executing model merely to pretend a stuck kernel retired.

## Current executable boundary

- `session.h/.cpp`: transport-independent C++ API and bounded ownership.
- `native_models.cpp`: existing native component adapters. Explicit qualified
  native libraries form the portable link contract. Mac keeps its CPU archive
  recipe; Windows and Ubuntu use CPU ASR/detectors and explicit Vulkan TTS,
  never fallback. CPU remains the omitted-argument default; accelerated proof
  launches name Vulkan explicitly.
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
  `aii_voice_models_load_with_backend` accepts only `cpu` or `vulkan`; Vulkan
  requires explicit process-local FP32/device-zero binding and fails on errors.
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

## Required before this replaces a checkpoint

The internal C ABI and the eight existing speech controls now have a tested
native carrier adapter. Independent stop-playback/cancel-synthesis, future
Finish cutoff admission, status/readback, session-scoped events and render
receipts execute without a second public transport. The adapter accounts for
actual pipe writes separately from core dequeue. Natural receipt completion
requires transport END; stopped receipts cannot revive output. Settings are
bound to the opening session; delayed old replies cannot configure a successor.
EOF is ordered after already-admitted requests, and final response writes keep
their deadline watcher through retirement. Source/binary/evidence:
`deliverables/common-native-sdk-20260912-r4/README.md`.

The development worker requires seven verified model paths and deliberately
refuses a no-argument installed launch. An optional final `cpu`/`vulkan` argument
selects TTS; omission retains CPU. Only fixed English Alba and `turn_pause_ms`
are wired. Unsupported operator settings fail explicitly;
they are not silently ignored or shown as effective. The complete signed
profile/catalog/package builder is still required.

Load/open initialize on their own owner; they are not inference-free controls.
Release is externally exclusive with calls on the handle and refuses live
workers. The model-free static core and the linked real-model library have
different qualification boundaries. See
`deliverables/native-c-embedding-20260912-r2/README.md`.

Long input now retains the exact overlapping raw samples needed by the next
recurrent window, without resetting caches, tokens or the source clock. The
one-minute recognition refusal is removed; the session's explicit 30-minute
bound remains. This is rolling input storage, not a fabricated turn boundary.

Synthesis accepts up to 8,000 UTF-8 characters, partitions losslessly at the
existing 180-character default and retains one generation/clock/END for the
whole reply. Every segment reuses the bound voice. Cancellation fences later
segments. The output queue has one shared 120,000-sample budget across all
generations, with a 15-second stalled-consumer deadline. A pathological single
segment still faults at 60 seconds; a whole reply is bounded at 30 minutes.

The passing Mac CPU build includes the hash-bound `capacity-history.patch`
overlay. Without it, a long prompt's larger cached graph changes the later
short reply's PCM. The overlay chooses the current text's canonical CPU shape
and reuses a graph only when that shape matches; model weights stay loaded.
Retained upstream sources and older checkpoint libraries are never edited.
This is tested history independence for the retained cases, not a claim of
bit-identical synthesis across accelerator implementations.

Evidence: `deliverables/native-session-continuous-20260912-r3` and
`deliverables/native-session-short-regression-20260912-r2`. The real model
gate observes 66.24 seconds as one turn, 134.24 seconds of complete output,
and the frozen 4-second recovery after interruption. All render receipts are
simulated; this is not a new installed or physical-audio qualification.

UID snapshot decisions/publication, reference-audio echo handling, complete
voice/language catalog and operator settings mapping are not yet composed here.
The proof's fixed English Alba voice does not replace the working multilingual
Mac checkpoint. The sealed C++ recognizer now verifies/maps its own weights and
passes frozen recognition and whole SDK cycles on all three desktops. That does
not yet establish the new complete native plugin's protected installed startup;
the prior passing Windows wall proof belongs to the older checkpoint.

Per-platform accelerator placement, complete native-model numerical panels,
installed SDK/browser conversation, physical playback, mobile app linkage and
signed packages remain separate gates. No human-level release claim follows
from these component or control tests.
