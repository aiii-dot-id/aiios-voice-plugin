# AII Voice: thin native T3 wrapper

Design checkpoint: 2026-09-08, corrected by explicit operator direction.
Newest execution: the [runtime-bound Mac carrier](../deliverables/plugin-sdk-engine-20260908/PACKAGED_RUNTIME_MILESTONE.md)
launches with no arguments and passes real speech under containment denying
development code and dependencies. The measured payload requires the existing
host's [verified native-tree installation extension](../deliverables/plugin-sdk-engine-20260908/NATIVE_RUNTIME_INSTALL_HANDOFF.md).
This is neither signed/installed activation nor physical-audio qualification.
Latest execution checkpoint, 2026-09-09: the
[normal carrier build](../deliverables/plugin-sdk-engine-20260908/PROMOTED_CARRIER_MILESTONE.md)
now uses landed SDK `1d17d60`, with default-path real-model proofs on Mac and
Windows and native Linux Describe. The
[Windows browser integration](../deliverables/plugin-sdk-engine-20260908/WINDOWS_JOINT_BROWSER_MILESTONE.md)
also passes its controlled positive and missing-receipt negative. Historical
“still required” statements below describe their dated checkpoints, not requests
to reapply already-landed fixes. Complete installed runtime/code packaging,
physical conversation, UID/mobile and human-quality qualification remain open.

The [development native T3 carrier now executes our real engine](../deliverables/plugin-sdk-engine-20260908/README.md);
this remains **not an installed or qualified plugin**. The shipping path is
**browser microphone/speaker -> Go AII OS host -> Plugin SDK -> voice engine**,
with output returning through the same host to the browser. The engine owns no
user audio device. Standalone execution remains the development/test boundary;
it is not an alternative shipping interface or a reason to postpone integration.

Measured readiness is now implemented using the actual SDK `ServeSessionReady`:
[two full resident speech cycles on Windows and Mac](../deliverables/plugin-sdk-engine-20260908/RESIDENT_REUSE_MILESTONE.md).
Keep a model-loaded activation across fresh user sessions; separate cold loading,
bounded admission and the later session-ready event. The marker is
`AII_VOICE_READY event=ready models_loaded=N accelerator=X probe_ms=N`.
No alternate public control method or device ownership is introduced.

The [engine's drain-to-Abort correction](../deliverables/plugin-sdk-engine-20260908/ABORT_DRAIN_MILESTONE.md)
keeps the existing `speech.session.close` operation and one cleanup owner.
An Abort is admitted during normal draining, fences output immediately, and
reports `aborted` only after native resources retire. It neither manufactures
client playback receipts nor certifies physical silence. Host-side pending-ack
interruption and the real browser/disconnect journey still need their joint
proof; model-worker tests do not establish those host behaviors.

The [proposed playback-report control is now implemented by the engine](../deliverables/plugin-sdk-engine-20260908/PLAYBACK_CONTROL_MILESTONE.md)
and tested over actual SDK framing with simulated client reports on Mac and
Windows. The [declared eight-control binding](../deliverables/plugin-sdk-engine-20260908/SDK41_BINDING_MILESTONE.md)
now pins shared SDK `41eef1f` and passes on both desktops. Installed browser/host
receipt binding remains outstanding. Ubuntu's newer receipt-control run refused at the unchanged free-VRAM
gate. None of those boundaries is relabelled browser playback qualification.

The [explicit input-completion milestone](../deliverables/plugin-sdk-engine-20260908/INPUT_COMPLETION_MILESTONE.md)
now runs on all three desktop SDK profiles, including silence and empty input.
The native engine emits `input_finished` after recognizer retirement and retains
the exact descriptor in `status.input_completion`; it never synthesizes an empty
transcript to resolve Finish. The [handoff](../deliverables/plugin-sdk-engine-20260908/IMPLEMENTATION_AGENT_HANDOFF.md)
specifies the native notification payload and host reconciliation requirements.
This SDK lifecycle observation is not automatically a new event accepted by the
standalone Voice Core trace validator, nor proof of installed host consumption.

## Authority and source boundary

Authoritative integration repositories are the build host `/home/user/work` and
`/home/user/work`. Read-only recheck on 2026-09-08: AII OS
`7b25fe5a82f161d2ee817dcda8b2b45e72cc2d1b`, SDK
`2acd6a0da9537d1cb74523447d6351e2b21cbe8e`; both had clean source worktrees.
The three SDK file digests below are unchanged. Current host/browser source
was inspected, not executed against our engine; the source-specific requests
below are integration work, not an end-to-end pass.

Later read-only checkpoint: clean AII OS `a9c64cfc9b3fc30045340ee837a92a8a70d61bdc`
lands request-1 reply routing. The [source review and engine handoff](../deliverables/plugin-sdk-engine-20260908/IMPLEMENTATION_AGENT_HANDOFF.md)
records the remaining turn-bound staleness, final-reply/close ordering and
browser routing cases. Do not equate this source landing with browser execution.

Earlier checkpoint: clean AII OS `03bbf32` includes the reported req1/2/3-A/4
landings plus bad-format custody and initial engine-event fan-out. The actual
host/SDK/MLX integration now has a reproduced first-reply-END lifetime defect
and an [isolated passing correction](../deliverables/plugin-sdk-engine-20260908/HOST_CONVERSATION_CANDIDATE.md).
The host files tested are byte-identical at this main revision; the correction
was not landed or deployed at that checkpoint. The [engine receipt seam](../deliverables/plugin-sdk-engine-20260908/PLAYBACK_RECEIPT_MILESTONE.md)
is tested internally, including real synthesis with simulated reports, and
full SDK speech regressions pass on Mac and Windows. Host/browser receipt
binding, actual browser final drain and remaining qualification still apply.
Descriptions below retain their original design/proposal context; they are not
a claim that every listed operation or UI journey has since been qualified.

Source checkpoint: clean `73348be` includes output-lifetime fix
`2f9911e` and the browser input half-close correction. The output defect above
is now historical, not an open request to reapply the candidate. Focused host
race tests and the Chrome/Firefox conversation-component test pass. A semantic
turn keeps the microphone stream open; explicit Finish fixes its one immutable
cutoff. Per-synthesis END keeps the output pump alive, while session terminal
retires it without waiting for the worker's physical pipes to close. The
[current ordered handoff](../deliverables/plugin-sdk-engine-20260908/IMPLEMENTATION_AGENT_HANDOFF.md)
retains session/turn binding, worklet-tail flush, final reply before drain-close,
browser-rendered receipts, UID, installed activation and target qualification.
No deployment or complete browser conversation/drain is inferred from these
component results.

Later source checkpoint: clean `be5c188` adds `fae35ac` session/reply binding
and microphone-tail flushing. Six focused tests pass uncached with `-race`,
including provenance and partial-tail cases in Chrome and Firefox. The handoff
records remaining natural-speech invalidation, admission/cancel ordering,
check/borrow pump races and old capture callbacks. These are source findings,
not newly executed failure probes; neither the final reply/drain nor installed
browser integration is closed. Ubuntu now also passes the full recorded-input
native SDK speech/reuse fixture in `linux-sdk-r2`; actual host containment and
browser-render evidence remain separate gates.

Earlier executable checkpoint, 2026-09-07 22:15 UTC:
AII OS `e6510c4bbcf3e0b754076057b52c474497589f7c`; SDK initially `b0d4996`,
then clean `02dd78e4cb011b28b5ae01223de890c8aba309de`. The intervening CLI report
commit does not alter the resident/audio files. Six focused resident-lane and
five voice-skeleton tests pass uncached under the race detector. This establishes
those tested SDK contracts, **not real-engine integration or deployment**.
Current `pkg/aiiosdk/session.go`, `audio.go`, `examples/voice-skel/main.go`,
`WRITING_A_PLUGIN.md` section 17 and host `internal/pluginhost/voiceaudio.go`
replace the September 5 local snapshot as the basis below. Mobile execution and
all adapter failure cases still need their actual target proofs.

Inspected SDK file SHA-256 bindings:

- `session.go`: `48ea927795af0db6384b09b4455cd83e4809ecd8a5aa612c709e6febc5457d9e`
- `audio.go`: `27152820a9afadcd773bff1ed447e859f3833c5eabf93639595932b90eb3e86b`
- `voice-skel/main.go`: `e20e9869b9d297001de056195a4e782e2ae5b281ee5acb5b17330d061d239392`

Reuse the SDK's separate resident-session dispatch mode: full-duplex framed
JSON-RPC 2.0, `invoke.call` requests, correlated admission replies and id-less
typed notifications. Ordinary serialized invocation is not a speech loop.
Use the SDK frame codec and registered event schema; do not invent framing,
capabilities, plugin manifest keys or audio message names here.

## Components and ownership

```
Browser / AII OS web UI
    microphone capture <-> browser playback + render/stop reports
                         |
          authenticated AII OS browser connection
                         |
Go AII OS host: audio endpoints, conversation, permissions, lifecycle
                         |
Plugin SDK: resident controls/events + separate host-owned PCM plane
                         |
Thin T3 adapter -> device-independent voice engine
                  STT / TTS / VAD / semantic turns / UID
                  native GPU backends where appropriate
```

The adapter translates contracts, owns no recognition model, enrollment store,
audio clock or second scheduler. The engine supplies recognition, synthesis,
VAD/turn decisions and optional UID. The browser owns device access, capture,
playback and the browser audio clock; the Go host owns authenticated endpoint
binding, negotiated conversion and validated client observations. AII OS owns
conversation, tools, grants, identity policy and rendering. It decides whether
to retain audio; the plugin cannot silently record or enroll a voice.

Native Core Audio/WASAPI/Pulse hosts remain useful laboratory adapters, not
dependencies of the shipping engine. A headless engine needs neither a physical
microphone nor a speaker. The Windows engine-host HDMI fault remains valid lab
evidence, but does not block browser playback on another endpoint. GPU-primary
inference does not imply native device ownership or GPU audio I/O.

The reference app's optional local chat client is NOT embedded in the plugin:
AII OS consumes committed transcripts and submits replies. This removes the
demo's dependency on a particular GLM model without changing voice capability.
Partial transcripts are observations, not tool/action instructions.

## Admission versus effect

All controls are bounded admission operations on one ordered control owner.
They return without waiting for models, an audio queue, inference mutex or a
worker to exit. Inference and playback must not share that owner. Admission
order remains stable; cancellation must not overtake the synthesis it names.
Bound queues and fault visibly on saturation; do not report acceptance before
installing a rejection fence. Connection loss after possible admission is an
unknown outcome, not evidence that no side effect occurred.

| SDK operation | Synchronous admission effect | Later evidence | Existing engine seam / adapter work |
| --- | --- | --- | --- |
| `speech.session.open` | reserve fresh session/activation and validated configuration | ready or failed, including exact models/backend and negotiated endpoint formats | WebSocket `start` already exists; split accepted-opening from readiness; no native device enumeration in the engine |
| `speech.session.synthesize` | reserve unique synthesis ID bound to session, turn and response; reject stale/duplicate IDs | chunks, synthesis end/cancel/failure | `LiveSession.submit_reply` / shared `SpeechOutput`; remove dependence on browser message shape |
| `speech.session.cancel_synthesis` | install synthesis-generation fence | worker retirement and exact generated/delivered counters | shared synthesis component exists; public reference `interrupt` currently combines controls, so an independent operation still needs exposure and proof |
| `speech.session.stop_playback` | fence engine output; host independently commands browser queue stop | validated browser render/stop report and retired output stream | expose output fencing independently; never wait for inference cancellation or claim the engine stopped a remote speaker |
| `speech.session.finish_input` | accept one immutable exclusive engine `end_sample` and input handle bound to this session; reject beyond boundary | exact tail processed, all recognition finals followed by `input_finished`; matching status descriptor, then host resolves eligible replies | implemented through the real desktop SDK, including empty input; host/browser completion consumption and reply drain remain required |
| `speech.session.close` | drain or abort accepted; refuse new work | terminal closed only after resources release or explicit failure | existing `end` and disconnect cleanup are not themselves SDK closure receipts |
| `speech.session.status` | bounded read of authoritative counters and state watermark | no extra model call or inferred completion | translate engine state; add activation/event-sequence reconciliation |
| `speech.session.playback_report` | validate instance/synthesis/stream and monotonic engine-clock rendered count; exact terminal retry is idempotent | client-evidence playback observation; normal drain waits for all terminal receipts | declared by SDK `41eef1f` and tested with real Mac/Windows engine workers; originating browser-instance and sink-clock binding still require installed proof |
| supervisor kill | out-of-band action, never JSON-RPC | verified process reap and separately checked audio cleanup | SDK activation kill on desktops; mobile reports only the lifecycle action actually available |

`cancel_synthesis` does not assert acoustic silence. `stop_playback` does not
assert inference retired. `synthesis_end` does not assert playback drained.
Interruption invokes both independently; opening input words and new recognition
remain admissible while the old output is being retired.

Close-drain requires a prior input cutoff. Control can arrive before the last
audio packet: remember the cutoff immediately, await the exact missing tail
within an explicit deadline, and fail a gap rather than pad away missing speech.
Finalization occurs once, including a final reply when eligible. An abort never
rewrites a failed/unknown physical cleanup as a clean close.

## Observations and UI

Reuse the SDK's snapshot axes: lifecycle, input, recognition, synthesis and
playback. Maintain a monotonic state watermark shared with the ordered event
stream, fresh activation/session/stream/synthesis identities, and no resurrection
from an older generation. A status snapshot may reconcile current state but
cannot recover a lost transcript or retroactively certify a dropped event.
Lost critical observations fault the session; never silently continue.

- **Idle:** session open, no utterance/finalization/response pending, no live
  synthesis, playback queue empty and host-confirmed stopped/drained.
- **Speaking:** playback is active or queued; recognition can simultaneously be
  active. Do not collapse full duplex into one mutually exclusive state.
- **Draining:** input half-closed or drain-close accepted with any unresolved
  recognition, response, synthesis or playback work.
- **Closed:** observed terminal release, not just an acknowledged close.
- **Failed:** loss/refusal/timeout with cause and unresolved resource state.

Show provisional/final transcript distinction, UID known/unknown/ambiguous,
collecting versus ready enrollment, device/provider selection and failure cause.
Do not display speaker identity as authentication or hide CPU fallback behind
an “accelerated” badge. State text must derive from the same data the API exposes.

## Audio and identity plane

Use host-owned V-E endpoints; no PCM/base64 in the control lane. **Use the
actual SDK API, not additional invented wire fields:** `Session.Audio()` returns
the inherited `AII_AUDIO_IN_FD` / `AII_AUDIO_OUT_FD` pair; its `Read`/`Write`
methods carry `AudioFrame{Kind, Stream, Seq, Start, PCM}`. The existing codec has
a 28-byte `AUD1` header, big-endian metadata and at most 64 KiB PCM. Kinds are
PCM, discontinuity and end; payload is interleaved signed PCM16 little-endian.
An end's `Start` is exclusive. Activation/session/capability association belongs
to the host-owned binding and lifecycle, **not extra per-frame JSON fields**.
The wrapper still must enforce channels, sequence, finite conversion, spans,
generation fences and bounded credit; the codec alone is not that state machine.

`OpenWithAudio` names `input_handle`, `output_handle` and `audio` with s16le
`input`/`output` `{rate, channels}`. Answer admission with our actual engine
clocks: **16,000 Hz mono input, 24,000 Hz mono output**. The host owns endpoint
rate conversion and finish-cutoff clock mapping. Convert PCM16 to/from neural
float32 once at the engine boundary; do not silently accept the device clock or
add a second resampler. Each synthesis gets a fresh, never-reused output stream
ID; stop/cancel closes and fences that stream, not every future response.
The example uses one rate in both directions; do not copy that simplification
into our different-rate engine. Its README still says “no audio” although its
current code/tests carry audio: use executable source, not that stale phrase.

Credits are bounded in samples, not arbitrary message count. Duplicate, overlap,
gap, wrong-generation and post-fence frames are explicit outcomes. Every rate
conversion accounts for actual source span, filter latency and final padding.
Test lost/unknown open responses with mismatched host/engine rates specifically:
the current host defaults unreadable/absent audio negotiation to endpoint
formats. Do not treat a lost negotiated answer as proof the engine uses those
defaults, and do not claim this failure seam closed by the successful mock test.

The host validates browser render/stop/drain reports against the active
session, output stream and samples delivered. A WebSocket write is delivery,
not playback; a browser render callback is a client observation, not verified
DAC progress or acoustic silence. Keep these measurements distinct. Physical
qualification still measures speech-onset-to-acoustic-stop independently.
Missing or stale client reports cannot certify drain or release an update pin.
Capture admission must remain independent of TTS/model latency.

Use a bounded browser capture/playback path, with sample accounting and retained
pre-roll. Record the actual capture-processing settings; a request for browser
AEC is not proof it is active or adequate. Do not blindly stack the native lab
AEC on already-processed browser input. If an engine path needs a render
reference, identify it from the playback actually scheduled, with its clock and
delay, not from all generated TTS. Browser/device permissions, autoplay, device
loss, suspend/resume and disconnect have visible outcomes. Explicit sink
selection is capability-detected; unavailable selection must not prevent the
operator from choosing clearly labelled browser/system-default playback.

UID is optional and off unless granted. Reuse `SpeakerTools` / the existing
speaker store, model binding and enrollment revision. Use exact source-bound
utterance spans; current live adapter limit is 1.995–30 seconds, three temporary
clips and 120-second expiry. Longer or unavailable clips report unavailable,
never an undisclosed crop. Identification cannot delay speech completion.

Explicit enrollment needs a separate host-authorized operation, selected
session/utterance, stable speaker ID, consent confirmation and exact audio hash.
Require the current three distinct recording rule; duplicate bytes cannot
manufacture independent samples. Removal/reset need explicit authority and
readback. Disconnected HTTP is not proof an already-admitted enrollment aborted.
Temporary clip expiry does not erase already-authorized evidence recordings;
recording retention and biometric enrollment are separate grants and stores.

Diarization/overlap separation, spoof/liveness and arbitrary voice cloning are
not implemented by the current live stack. Do not advertise them until their
end-to-end interfaces, data and quality gates pass. UID remains personalization,
not an authentication factor. These are completion obligations, not features
silently removed from the program.

## Platform packages and updating

One engine API and SDK adapter, platform-specific inference backends, and browser
audio on every shipping target. Native device adapters are laboratory-only.
Suggested package shapes below are intended deliverables, not present support:

| Family | Native surface | Neural execution | Full payload required |
| --- | --- | --- | --- |
| macOS | signed engine/helper through SDK; browser audio | MLX Metal; declared Core ML/CPU specialists | relocatable engine, dependencies, weights, configuration and browser journey; notarized distribution |
| Ubuntu | engine executable/library through SDK; browser audio | CUDA on RTX 4070 Ti; declared CPU DSP | CUDA-compatible runtime, same complete browser/SDK session and cleanup |
| Windows 10/11 | engine process/library through SDK; browser audio | measured CUDA/DirectML and declared CPU specialists | runtime DLL closure, browser session on Windows 11 GPU VM and separate Windows 10 compatibility evidence |
| Android | host-supported SDK/mobile binding; web UI audio | measured delegate/Vulkan/other supported GPU export | complete models/frontend, Pixel host integration, browser permissions and lifecycle proof |
| iOS | signed host/framework SDK binding; web UI audio | Core ML GPU where verified, explicit CPU work | complete exports, persistent cancellation/state, actual iPhone browser/host journey; no desktop subprocess assumption |

Desktop and mobile packaging share immutable system identity, but cannot share
unportable Python/MLX weights by relabeling a tarball. Existing phone probes run
one component only. Keep Windows 10, Windows 11, Pixel 10 Pro and Pixel 11 Pro
separate target records even though they belong to two platform families.
Record browser/client OS separately from engine OS and accelerator. A phone
browser driving a Mac engine proves that client route, not local phone inference.
The five-platform local goal still requires the engine on each target. A browser
page does not substitute for a native/mobile host capable of loading the engine.

Each qualified package binds exact source, runtime/dependency closure, every
model/tokenizer/frontend/policy and conversion, precision/provider/fallback,
license notices, platform report, protocol conformance and human evaluation.
Local development bundles are explicitly not qualified installers. Upstream
checkpoints retain their provenance/terms; do not label an aggregate of those
weights as a new Apache-trained checkpoint. Nothing is published by this design.

Updates pin the old activation until all sessions and audio endpoints are
resolved. New files stage under a new immutable identity; no replacement beneath
running models. Verify new health/bindings before changing the selected version.
Rollback preserves identity/enrollments and records any incompatible store or
policy migration; never silently reset them. Transport loss alone releases no
pin. Desktop verified reap and mobile lifecycle failure are different facts.

## Adapter acceptance: execute, do not infer

1. Real SDK framing and independent host/guest processes: admitted control while
   inference deliberately blocks; async events still delivered and correlated.
2. Stop and cancel independently under saturated output; no old PCM after fence;
   no dropped onset words; fresh synthesis and recognition both recover.
3. Final input cutoff before last audio packet, exact and incomplete tails,
   duplicate finish, drain/abort, disconnect and device loss.
4. Events/snapshots reordered, duplicated, missing and foreign; no obsolete state
   resurrection; terminal completion exactly once, no synthetic “closed”.
5. Endpoint capability misuse, malformed/oversize frames and bounded resource
   exhaustion; no audio routed to a default/foreign device on failure.
6. Explicit UID opt-in/enrollment/removal/reset, known/unknown and late results,
   source-audio identity and persistence across process restart.
7. Activation-pinned update during a long session, failed update, rollback and
   out-of-band kill; verify browser capture/playback and endpoint release, not
   just process return. No engine-side device defaults should be touched.
8. Run the same sealed standalone scenarios through the AII OS web UI and SDK.
   Bind browser/host/SDK/engine source and models,
   compare sample accounting and event outcomes, measure wrapper overhead and
   operator-perceived speech. Passing SDK mocks cannot substitute for this.

Freeze numeric overhead limits before this comparison, using standalone
development baselines. No latency or quality improvement is projected as a fact.

Implementation order: bind the now-revalidated SDK -> expose independent existing
engine controls -> wire SDK/audio adapter -> execute these eight proofs -> package
with qualified target artifacts. Keep adapter and model logic separate so an
AII OS implementation change cannot conceal a voice model failure.

## Source-bound requests to the AII OS / SDK implementation agent

Operator authorizes requests to either repository. The following are requested
integration changes against the September 8 revisions above, not changes made
by this voice-platform task. Reconcile current bytes before implementation.
Extend the existing owners and registries; do not create a second transport,
session manager, credential store or audio service.

1. **AII OS: route application replies to resident engine TTS.**
   `internal/app/voice_session.go:observeEngine` forwards final transcripts to
   `observeVoice`, but the inspected production Go source has no call to
   `VoiceSession.Synthesize`. `static/ws.js` routes response text to
   `static/voice.js:speak`, which calls browser `speechSynthesis`.
   Bind an eligible host-authored response to its voice session/turn and fresh
   synthesis ID, submit through the existing SDK operation, and suppress browser
   TTS for that session. Preserve any explicit non-plugin fallback separately.
   **Proof:** two different actual conversation replies arrive as plugin PCM;
   no duplicate/system speech; stale replies after barge-in or close cannot play.

2. **AII OS: add continuous duplex alongside push-to-talk.**
   `static/voice.js` currently starts capture on pointer-down, finishes on
   pointer-up, and batches through `createScriptProcessor(4096, 1, 1)`.
   Add explicit Start / Finish input / Abort for a resident conversation; keep
   capture active during replies and commit turns from engine semantics, not
   pointer release. Use bounded worklet capture, preserve pre-roll and tail, and
   distinguish browser AEC settings/reference availability from tested quality.
   **Proof:** several turns without holding a button, natural pause continuation,
   spoken interruption with opening words retained, and no microphone left open
   after disconnect, denial or page teardown. Keep push-to-talk usable.

3. **AII OS + SDK contract: make browser playback observable and fenced.**
   `browserSink.Write` currently proves only a socket write. `Playback.stats`
   stays client-local; `VoiceRequest` has no playback-report action. The browser's
   single `fenced` value is cleared on an end or another stream. Bind stop/drain
   observations and cancelled-output tombstones to the session/generation,
   including cancel before first PCM. Retain fences until old delivery cannot
   re-enter playback. The host combines client render observations with engine
   state; the engine never invents speaker completion. Reuse existing snapshot
   axes; extend the registered host/SDK contract only where required.
   **Proof:** delay the first PCM until after cancel; deliver old PCM after a
   new stream; withhold a drain report; stall inference while stopping playback.
   No revival, false idle/closed or premature update release is permitted.
   **Current source-class falsifier:** executing the actual `Playback` class
   with a simulated AudioContext reproduced both counterexamples: stop before
   first PCM still schedules that PCM; old stream 10 still schedules after
   stop and recovery stream 11. Tested `voice.js` SHA-256
   `a9fcd09d9d4ed674cd32f01f6208082fcee18ab04cc701169c4deb265b293380`.
   This establishes the class-level fence gaps, not a physical browser result.

4. **AII OS + SDK contract: explicit audio negotiation before pumping.**
   `pluginhost/voiceaudio.go:engineFormats` currently substitutes endpoint
   formats for absent/unreadable results, including an unknown open outcome.
   Our input is 16 kHz mono and output 24 kHz mono; browser capture may differ.
   Require a confirmed format binding before audio flows. An unknown open must
   retain custody and reconcile/abort, not guess sample rates. Keep one conversion
   owner, correct finish-cutoff mapping and bounded audio independent of control.
   **Proof:** a 48 kHz browser, 16/24 kHz engine, lost open reply, malformed format,
   cutoff before final packet, missing tail and saturated sink. No wrong-speed
   audio, concealed gap, control blockage or falsely released session.

5. **AII OS: deliver live engine state and speaker evidence to the web UI.**
   `observeEngine` currently consumes only `transcript_final` and extracts
   `text`/`speaker`; `VoiceSessionState` exposes open/closed/refused plus a label.
   Fan out from the existing observation owner: provisional/final transcripts,
   turn events, lifecycle/substates, failures and optional UID observations with
   source span, score and model/enrollment binding. Do not add a competing reader
   that steals events. Recognition remains participant evidence, never operator
   authority; enrollment/removal retain explicit host grants and readback.
   **Proof:** concurrent recognition/playback is visible, late/foreign events are
   refused, unknown UID is normal, final text is not duplicated, and event loss
   cannot be rendered as a successful idle state.

**Voice-engine work owned here:** device-independent SDK adapter over the
existing recognition, synthesis, UID and turn components; independent admission
and cancellation; honest model/provider identities; exact input/output spans;
bounded worker lifetime; all engine-side failure and recovery proofs. No native
microphone/speaker enumeration, hidden chat model or second AII OS policy loop.

**Joint exit:** one real browser -> AII OS -> SDK -> engine -> browser conversation
with ordinary speech, two distinct replies, spoken interruption, retained opening
words, a complete recovery reply and final drain. Bind every component revision,
separate delivery/render/acoustic timings, and repeat on each local target.
Mock transport tests and native laboratory runs support this exit but cannot
replace it. Mobile SDK loading/lifecycle must be executed on the real hosts;
desktop inherited descriptors alone do not prove mobile integration.
