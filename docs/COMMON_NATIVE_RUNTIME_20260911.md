# One native voice engine across five targets

Operator direction, 2026-09-11: get a common runtime for macOS, Windows and
Ubuntu, also Pixel 10/11 Pro and iPhone 17/18 Pro where possible. This records
the implementation direction, not a claim that the complete runtime exists.

**Operator clarification, 2026-09-13:** homogeneity is a preference, strongest
across Windows/Ubuntu/macOS, not a release requirement. Heterogeneous engines
or component backends are permitted where measured quality, latency, memory,
energy or installed footprint justifies them. The common boundary is the plugin
interface and full feature/behavior contract, not mandatory identical internals.
The shared C++ core below remains our current implementation, not a constraint
that may override a better qualified platform implementation. See the binding
[compute and selection requirements](AII_VOICE_COMPUTE_PLAN.md).

## Decision

Latest execution, 2026-09-12 13:10 UTC: five-model SDK conversations and UID
known/unknown/unavailable cases now pass on all three desktops. A real unsigned
Mac native checkpoint is assembled: zero-argument startup, ten selectable
voices, seven executable settings, stable-repeat/seed/temperature audio and
source/model/runtime/bundle readback pass. Ubuntu and Windows's updated builds
also pass the thirteen actual voice cases. Independent readback verifies ten
byte-identical central production files, actual audio/settings/events on all
three desktops and retirement of the recorded Windows/Ubuntu owners. The native
engine no longer needs an installed Python environment.

This closes a native private-checkpoint milestone, not the complete product.
The candidate is English-only and CPU on Mac; richer existing checkpoints stay
intact. Operator signing and installed browser proof, enrollment editing,
full echo/double-talk, complete language/GPU/mobile composition remain. See
`deliverables/common-native-checkpoint-macos-20260912-r1/README.md` and the
immutable exchange handoff `20260912-1303-native-candidate-handoff.md`.

Latest execution, 2026-09-12 06:24 UTC: the native five-model Mac engine now
uses the actual Go SDK to read host-owned enrollment snapshots and publish
speaker observations against public final sequences. Known/unknown/unavailable
cases, control during a delayed read, two complete conversations and repeated
common native tests on all three desktops pass. The final evidence includes a
clean rebuild and compiler-derived dependency inventory. No new public SDK API
was needed. This closes the development read/observation adapter, not enrollment
mutation, installed qualification, the full catalog or mobile composition. See
`deliverables/native-uid-sdk-20260912-r6/README.md` for the exact boundaries.

Latest execution, 2026-09-12 05:43 UTC: the shared session now also composes
real native UID on Mac, with exact final-span attribution and independent
cancellation/drain ownership. All 125 frozen decisions are unchanged; two
known-speaker rejections remain. The new core/policy contracts pass on all
three desktops, but the UID host-snapshot/private-adapter/public-event path
and five-model Windows/Ubuntu runs are not yet qualified. See
`deliverables/native-speaker-session-20260912-r3/README.md`.

At 05:05 UTC, Ubuntu joined the passing four-model SDK conversations on Mac
and Windows, with the same common source, exact within-backend recovery,
spoken interruption and receipt-held drain. This supersedes the older Ubuntu
composition gap below; it does not close UID/echo/full-catalog/mobile gates.
See `deliverables/native-desktop-linux-20260912-r1/README.md`.

Latest execution, 2026-09-12 04:42 UTC: the same common C++ session sources now
execute two actual SDK conversations on Mac CPU and Windows CPU/Vulkan. Both
pass exact recovery, opening-word, spoken-interruption, Finish and receipt-held
drain gates. The Windows backend's history-dependent prompt geometry was fixed
in an isolated two-file candidate; the unchanged exact PCM assertion now passes.
Readiness is 35.728 s and completed-reply RTF 0.474–0.545 in this ordinary VM
observation, not an installed or human-level result. Ubuntu full composition,
complete UID/echo/catalog/language capabilities and mobile app runs remain.
Evidence and explicit limits:
`deliverables/native-desktop-windows-20260912-r3/README.md`.

Latest execution, 2026-09-12 03:48 UTC: the native recognizer now owns sealed
read-only external-initializer loading and passes unchanged recognition
trajectories on Mac, Ubuntu and Windows. The same Mac native SDK conversation
still passes. Physical Pixel passes the frontend/synthetic-loader tests and
iOS arm64 links; neither result qualifies full mobile speech. See
`deliverables/native-asr-portable-20260912-r3/README.md`, including the passing
independent evidence readback and the remaining installed/GPU/UID/profile gates.
Remote collection and owned-task retirement were verified at 03:58 UTC after
connectivity recovered; no live identity or checkpoint was changed.

Latest execution, 2026-09-12 03:08 UTC: the common C++ owner now has its native
private worker connected to the existing Go SDK. Real Mac speech passes twice,
including exact recovery PCM and receipt-held draining. The same private
transport compiles and passes its model-free tests natively on all three
desktops, including Windows blocked-pipe cancellation. This is not the complete
profile, a signed plugin or new mobile qualification. See
`deliverables/common-native-sdk-20260912-r4/README.md` for the exact boundary.

Latest execution, 2026-09-12 02:26 UTC: the common owner is now exposed through
an implemented C ABI. Real Mac model execution and the original short/long
regressions pass; the same control/C-caller bodies run repeatedly on all three
desktop targets and physical Pixel. iOS compiles only. This closes the internal
embedding seam, not the public carrier/profile/device qualification. Evidence:
`deliverables/native-c-embedding-20260912-r2/README.md`.

One C++ engine core, exposed through a small internal C ABI, with platform
acceleration adapters. One voice plugin identity and feature contract, with
OS/architecture-specific artifacts. Reuse the existing Plugin SDK controls,
events, audio plane and render receipts; no new public SDK mechanism is needed
merely to hide the engine's implementation layout.

The common runtime is not one universal binary and need not be one inference
library. Use ONNX Runtime's native API for the graphs it executes well, and
the native streaming tensor backend for autoregressive/flow/codec models when
that is the proven fast path. Do not force every model through ONNX or create
a separate inference backend per feature without measurements.

Desktop: the existing Go T3 carrier drives the native core. Mobile: the same
core links into the app through its supported native extension path and thin
JNI/Swift adapters, with model assets separately bound. This is a build and
packaging distinction, not a second speech state machine. No lab Python,
runtime package installation or developer source tree in the public engine.

## What is common and what varies

| Common implementation | Deliberately platform-specific |
| --- | --- |
| Audio sample clocks, pre-roll, VAD/semantic pause, turn commitment | Accelerator registration, graph compilation and device placement |
| Recognition results, stable voice selection and language validation | OS/architecture libraries and toolchains |
| Generation-scoped cancellation, output rejection fences, drain/Abort | Desktop containment versus mobile app lifecycle |
| UID embedding/decision semantics and host-owned enrollment snapshot | Bounded backend-specific kernels where numerical parity requires them |
| Settings/effective readback, capability catalog, fixture suite | Signed platform packaging and hardware qualification |

Browser/UI owns capture and playback. The engine consumes audio from the
host-owned endpoint plane and returns PCM; it must not open another microphone
or speaker. Render-reference echo handling must be agreed with the host before
we freeze its format. Receipts describe rendering but are not an echo waveform.
Apply effective echo handling before VAD, recognition and UID. Never mask echo
by ignoring speech while speaking or by dropping text that resembles a reply.

Native asset access also needs one platform wrapper: usable I/O paths and
physical identity keys are different operations. The protected Windows gate
found `std::filesystem::weakly_canonical` denied inside the native TTS loader
after Python's equivalent issue was corrected. Handle-resolved identities
must avoid requiring mount-manager access while retaining actual access errors
and alias/volume distinction. Do not solve this with broader sandbox grants.
The concrete source locations and failed gate are retained in
`deliverables/checkpoints/windows-native-lean-wall-20260911-r2/README.md`.

The desired silence pause is audio-clock-based and adjustable. Preserve the
current semantic extension and opening pre-roll; frontend chunk size must not
change commitment. The current recognition segment bound must be represented
as a segment boundary, not silently treated as the person's conversational end.

UID is an observation, not permission. Known, unknown, insufficient and
ambiguous results must remain distinguishable. Enrollment and authorization
stay with their existing host owners; no second enrollment database. A late
speaker result amends its transcript, never creates an extra command turn.

## Starting components, checked against source

| Existing code | Concrete remaining gap |
| --- | --- |
| `runtime/native_asr`: C++ frontend, caches, decode loop, cancellation, C ABI | Current C++ path is CPU-only; integrate provider selection and prove actual graph placement without changing transcripts. Preserve model/cache/finish-padding semantics. |
| `runtime/native_vad`: native Silero/ORT with sample accounting | Compose under the common input owner and rerun silence/double-talk/pause cases. |
| `runtime/native_uid`: native embedding and frontend; bounded cancel/recovery | Compose decision/snapshot publication with host UID, preserving unknown/ambiguous decisions. |
| `runtime/native_endpoint`: native semantic endpoint | Mac lightweight frontend passes retained exact checks; Windows uses the larger ATen frontend because the smaller port failed parity. Do not remove it merely to reduce bytes. |
| `runtime/native_pocket`: native streaming TTS and generation fence | Windows and Android build recipes are still separate; Windows recipe hardcodes `.lib` paths. Consolidate without losing the measured streaming-tail optimization. |
| `runtime/plugin_engine`: working complete checkpoint owner; `runtime/native/session/worker.cpp`: native SDK counterpart | Native STT/VAD/endpoint/TTS, interruption, Finish, status, receipts and retirement now execute through the SDK. Compose UID, echo, full catalog/settings and per-platform model bindings before replacing the checkpoint. |
| Mac CP3: Qwen/MLX multilingual synthesis and selected references | Still the private Python checkpoint. Native equivalent must preserve its languages, voices and audible quality before replacing it. |

`runtime/native/CMakeLists.txt` now composes the existing four ORT components
without copying their implementations. This removes four separate build
entry points for an integrated build but does not itself compose a conversation.
Existing per-component entry points remain usable for numerical regressions.

## Acceleration candidates, not automatic qualification

| Target | Primary paths to measure |
| --- | --- |
| macOS Apple Silicon | Metal for the streaming tensor backend; Core ML for suitable ONNX graphs. Keep the working MLX checkpoint until native parity passes. |
| Windows | Vulkan for the measured streaming TTS path; CUDA or DirectML for suitable graphs according to actual device/support. Use the normal interactive application context on the VM. |
| Ubuntu | CUDA on the RTX workstation; native Vulkan where that graph's measured path warrants it. |
| Pixel 10/11 Pro | Native Vulkan for the tensor path; evaluate LiteRT GPU for suitable converted graphs if it earns its additional backend. Do not assume a Qualcomm QNN path applies to Tensor. |
| iPhone 17/18 Pro | App-linked Metal tensor backend and Core ML for suitable graphs, with bounded-shape specializations where measured. |

Small control/VAD operations can stay CPU when that gives lower measured
latency/energy. A listed provider or requested GPU is not evidence that a
model ran there: record actual node placement, fallbacks, transfers, memory
and whole-session behavior. Backend failure must be visible, not an undisclosed
CPU retry. Do not normalize Windows VM measurements by an assumed multiplier.

## Preserve capability while converging

The fast native Pocket profile is not a multilingual Qwen replacement.
It can ship as an explicitly named compact English profile once its complete
gate passes. A shared runtime must expose truthful profile-specific voices,
languages and parameters; it must not show a voice or language its active
model cannot execute. Retain CP2 voice IDs and operator selections in CP3.

Keep the working private Mac release moving independently: CP3's 12 voices
and nine controls are packaged and sent to the build host for the operator signing ceremony.
Do not hold that checkpoint for completion of native/mobile work. It is not
the public native release and does not close echo or host-integrated UID.

## Implemented common-build waypoint, 2026-09-11 23:20 UTC

The shared build is now exercised, not just proposed:

| Target | Evidence established in this waypoint | Still outside this gate |
| --- | --- | --- |
| macOS M3 | Same four components build; four native contracts pass; path helper also passes ASan/UBSan | One Python-free session owner, native multilingual TTS and whole-engine provider qualification |
| Ubuntu the build host | Same build and four contracts pass; 884 native VAD blocks within 1e-6 of Mac, all threshold decisions equal | Full ASR/UID numerical panels under this build, TTS composition and CUDA placement |
| Windows VM | Native platform-path fix compiled and tested; two recorded-input SDK speech cycles pass with four completed WAVs byte-identical to r6 | Protected startup fails inside ORT external-weight path handling; diagnostic-only r9 exposes `weakly_canonical: Access is denied`; Python owner remains |
| Pixel 10 Pro | Same components cross-build; four native contracts and 884 VAD blocks run on the physical phone, probabilities exact to Mac fixtures | Complete mobile session, full-model panels, GPU/audio/lifecycle qualification |
| iPhone 17/18 Pro, Pixel 11 Pro | No new whole-engine evidence in this waypoint | Same-core app integration and actual target qualification |

Detailed source-bound records: `deliverables/common-native-components-20260911-r2`,
`deliverables/common-native-linux-20260911-r1`,
`deliverables/common-native-android-20260911-r2`, and
`deliverables/native-lean-runtime-windows-20260911-r8`.

The native Windows TTS filesystem blocker described above is now passed in
the real protected startup path without expanding the wall. The next failure
is the independent recognizer. Its previous error handler erased child stderr
and overwrote prior errors with generic EOF; those observation bugs have
focused regressions and a separate diagnostic candidate. That candidate now
exposes `weakly_canonical: Access is denied` inside ORT initialization of the
external-weight recognizer. Both original source-root permissions and runtime
hashes remain unchanged, and the failed gate is retired. See
`deliverables/checkpoints/windows-native-lean-wall-20260911-r4/README.md`.
Preserve external-file containment when fixing this loader; do not turn a
filesystem compatibility defect into a broader sandbox permission.

One common API is not yet one frozen dependency set: the current gates use
Mac ORT 1.29.0 with API 24, Ubuntu ORT 1.24.2 with API 24, and Android API 23.
Record these differences until one tested per-platform dependency matrix is
frozen. CPU correctness is not GPU placement or real-time whole-system proof.

### Loader correction carried toward the native core

Windows r12 now supplies the exact 640 external encoder tensors with ORT's
supported initializer API, after validating the complete pinned graph, index
and checkpoint. The unchanged ordinary SDK speech gate passes twice, with all
four completed WAV files byte-identical to r8. The Mac counterpart preserves
all ten recognition cases and every partial transcript. No provider or
recognition-timing change accompanies it. Protected startup r5 failed at the
unchanged 180-second readiness deadline: Vulkan discovery was logged, but no
readiness or new underlying cause. The trace-only r13/r6 gate now confirms
TTS construction (3.391 s) and semantic endpoint construction (1.218 s) finish
inside the unchanged wall. The next call, `ResidentSTT.await_ready()`, does not
return before the host deadline. That call includes its error cleanup; the
child's exact loading/retirement failure is not yet localized. Neither ordinary
success nor the missing prior exception closes protected startup. See
`deliverables/checkpoints/windows-native-lean-wall-20260911-r6/README.md`.

The shared C++ recognizer still opens the ONNX filename directly; it has **not**
yet acquired this initializer binding. Its Windows integration must carry the
same sealed tensor mapping and lifetime rules through ORT's C++ API, then run
the same full recognition and sandbox tests. A passing Python checkpoint is
not evidence that the C++ loader is fixed. Do not reintroduce the canonical-path
dependency while composing the common core.

This method keeps user-owned mapped buffers and lets ORT copy them before
optimization. Mac peak RSS was 7.66 GB. Ordinary Windows spawn-to-ready was
63.06 s versus r8's earlier 45.14 s observation; this is a compatibility fix,
not a performance gain. The retained observations must guide the next bounded
memory/startup optimization without changing models or loosening the wall.

### Checkpoint lineage correction, 2026-09-12

The lean Windows builder preserved the old serial carrier verifier. The newer,
bounded verifier existed and had native Windows tests, but those bytes were
absent from r13. Therefore the last recognizer-join marker cannot be interpreted
as proof that recognition occupied the entire host readiness window. A later
clock bridge suggests most of that window preceded model construction; a
same-run trace is the stronger evidence.

R14 carries the existing complete-inventory verifier and static carrier phases
into that lineage. Its 6,504 runtime payload files, model owners, SDK and model
data are unchanged; only the carrier and its binding metadata change. The
ordinary trace measures complete verification at 1.057 s and reaches STT
readiness. Full ordinary speech, the unchanged protected-startup gate and
retirement are independently assessed in its delivery record. None of this
relabels the Python owner as the common native session.

This also makes the common-runtime implementation boundary explicit: platform
adapters own filesystem/accelerator differences, while complete asset binding,
session lifetime, cancellation and speech semantics have one implementation.
The shipping build must compose tested components and their tested adapters;
carrying a model improvement must not silently restore an older platform fix.

The resulting r14/W2a gate **passes**: 147.286 s to warm readiness under the
unchanged 180-second AppContainer limit. Verification itself consumes 78.393 s
there, versus 1.057 s ordinarily. Two ordinary SDK speech cycles pass and all
four completed replies are byte-identical to r12. Full protected conversation
is still a separate gate. The close census/WMI timing discrepancy and later
all-PID retirement readback are retained in
`deliverables/checkpoints/windows-native-lean-wall-20260911-r7/README.md`.
No timeout/grant/model change was needed. The Python-free common owner remains
the next implementation milestone; the passing checkpoint is preserved.

## Concrete next delivery sequence

### Three-desktop common SDK composition reached, 2026-09-12 05:05 UTC

Ubuntu now executes two real-model SDK conversations on the same common C++
session implementation as Mac and Windows. The 26 implementation/build/test
files match across the frozen archives; a later README update is recorded as
a documentation difference. The native owner preserves exact opening words,
interruption, within-backend recovery PCM, Finish, receipt-held drain and exit.
Actual loaded libraries are hash-checked. TTS uses explicit Vulkan on RTX 4070
Ti, recognition/detectors CPU; no Python or Torch is loaded in that engine.

Warm Ubuntu first PCM is 32–192 ms, completed RTF 0.072–0.110, readiness 22.95 s.
The earlier direct process's first-use PCM latency is 3.485 s; these are separate
observations, not interchangeable estimates. This is recorded audio/simulated
receipts, not physical playback. See the exact scopes, failures and independent
audit in `deliverables/native-desktop-linux-20260912-r1/README.md`.

This closes item 1's initial native SDK-conversation gate below. Item 2 is still
incomplete: the common profile must include UID, echo and all operator choices.
Keep working checkpoints until that fuller profile passes installed gates.

### Continuous conversation reached, 2026-09-12 01:55 UTC

The 59-second recognition refusal and single-segment reply limit below are now
closed in the native core. Bounded raw-PCM retirement preserves recurrent
recognition state without inventing turn boundaries. The complete model gate
retains a 66.24-second utterance as one final and synthesizes a 134.24-second,
18-segment reply with one generation/clock/END. Cancellation during its next
reply's second segment suppresses later segments and preserves exact recovery.
No model or voice was changed to reach this result.

The run exposed a real TTS cache-history dependency: reuse of a larger CPU
graph altered the PCM of a later shorter prompt. An isolated two-file overlay
selects the current segment's canonical graph shape, retaining reusable weights.
The original failure and the passing unchanged recovery assertion are retained.
No broader claim about perceptual improvement or all GPU numerical behavior is
made from this deterministic CPU case.

The updated control/text bodies pass on all three desktop targets and physical
Pixel; iOS compiles. Real long speech is measured on Mac CPU only. Evidence:
`deliverables/native-session-continuous-20260912-r3/README.md`.
Proceed to the existing SDK binding and complete profile integration; do not
label the common core a signed plugin or replace CP3 before those gates.

### Native composition reached, 2026-09-12 01:15 UTC

`runtime/native/session` now supplies the common four-worker owner. The real
Mac CPU gate joins STT, VAD, semantic endpoint and TTS, preserves the exact
14-word transcript and complete recovery audio across input packet sizes,
and reopens the same models after Abort. The same source's model-free control
contracts run on Mac, Ubuntu, Windows and physical Pixel 10 Pro. iOS arm64
compiles but has not run on the phone. Three compiled regressions fail by name.

This closes the first composition seam, not the whole item 1 exit gate below:
the recorded-input **SDK** conversation must still run on the new native owner.
Its current short-generation bounds, missing long-text/recognition rollover,
UID, echo, settings/catalog mapping, C ABI and accelerator/installed integration
are explicit in `runtime/native/session/README.md`. The retained private Mac
checkpoint and Windows/Ubuntu paths are not replaced by this narrower profile.

Evidence: `deliverables/native-session-20260912-r3/README.md` and its independent
readback audit. This is real component execution, not human-level qualification.

1. **One build graph, then one native session owner.** Build the existing ORT
   components together using bound dependencies, preserve their original
   regression tests, and integrate one owner with bounded queues and independent
   interruption. A library collection is not sufficient: the exit gate is the
   recorded-input SDK conversation with final transcript, real audio, barge-in,
   exact opening words, recovery, Finish tail and Abort, without Python.
2. **Complete desktop native profile.** Consolidate native TTS build/streaming
   path, attach VAD/semantic pause and UID snapshot decisions. Run the same
   whole-engine test on macOS, Windows and Ubuntu. Preserve a working checkpoint
   when an individual platform fails; publish the precise failed boundary.
3. **Multilingual quality-preserving profile.** Port the current capable model
   or adopt a proven native equivalent, comparing the same speech/language and
   interruption fixtures plus listening judgments. An English-only success
   cannot close this gate. Tokenizer, voice conditioning, acoustic generation,
   codec, final samples and settings all belong in model parity.
4. **Signed desktop candidates.** Freeze exact libraries/assets and deliver one
   artifact per OS/architecture; independent unpack/readback, protected startup
   with the existing budget, installed browser conversation, settings/effective
   state, UID, echo/double-talk, disconnect and rollback. Test all five features
   together. Python-free is structural, not a synonym for good audio.
5. **Mobile builds of the same core.** App-link with native adapters, then run
   those same feature gates on real Pixel and iPhone hardware, including thermal,
   memory and audio-route/lifecycle interruptions. A cross-build or a UID-only
   device probe is not a complete voice release or proof on the next generation.

No estimate of speed, download size or human-level quality follows from the
architecture alone. Use measured per-artifact cold start, first partial/PCM,
sustained RTF, interrupt latency, word/voice quality, memory and installed bytes.

## Primary references checked 2026-09-11

- [ONNX Runtime execution providers](https://onnxruntime.ai/docs/execution-providers/):
  shared native API, graph partitioning and possible CPU fallback.
- [Core ML provider](https://onnxruntime.ai/docs/execution-providers/CoreML-ExecutionProvider.html):
  macOS/iOS native APIs and compute-unit/shape options; explicit registration.
- [Android NNAPI](https://developer.android.com/ndk/guides/neuralnetworks): deprecated
  since Android 15; do not select it as our new performance-critical baseline.
- [LiteRT Android](https://developers.google.com/edge/litert/android): candidate
  native/mobile acceleration route, subject to model conversion and parity.
- [ggml source](https://github.com/ggml-org/ggml): reuse the native tensor backend
  already in our streaming TTS work, with bound source rather than moving HEAD.
