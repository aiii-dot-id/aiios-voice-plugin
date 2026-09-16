# Voice plugin: operator-visible capabilities and controls

## Current release candidate: desktop Beta 1 (2026-09-16)

The current unsigned package is `aa3ae750...`, with real-model component runs
on macOS, Windows 11 VM and Ubuntu 24.04/26.04. See the
[complete publication handoff](../deliverables/desktop-publication-handoff-20260916-r1/README.md)
for exact bytes, declarations and remaining release gates. No new installed
Ember/Ivy version is asserted here.

Its seven declared controls are: ten named speaking voices (default Alba),
English speaking/recognition language, always-active VAD pause 320–5000 ms
(default 768), VAD threshold 0.05–0.95 (default 0.5), voice variation 0–1
(default approximately 0.3), and synthesis seed 0–4294967295 (default 20260908).
Changes take effect on the next session. VAD pause does not delay barge-in.

For this candidate, the historical enrollment instructions below are replaced
by explicitly consented guided capture, durable pending evidence and later
confirmation while the microphone is closed, including after process restart.
The [host join contract](GUIDED_ENROLLMENT_HOST_JOIN.md) names exact arguments
and lifecycle. Six discoverable speaker operations and their schemas are in
the package. Stable speaker IDs are not display labels or command authority.

Host Add speaker UI and all/only/ignore transcript filtering remain required
release work. Do not suggest an installed button, working filter or finished
enrollment journey until that host integration is actually demonstrated.

## Historical checkpoint: CP3 (2026-09-13)

### Current installed and tested boundaries

**Live defect and repair, 2026-09-13:** the installed enrollment1 speaker
family refuses host-added `_host_now_ms`. The earlier SDK fixtures omitted
that metadata and did not prove installed tool admission. The
[Mac enrollment3 carrier repair](../deliverables/cp3-host-metadata-proof-20260913-r2/README.md)
passes corrected real-model enrollment, spoken recovery and all settings;
it remains unsigned/uninstalled. A guide update alone does not change Ember's
installed behavior. The separately tracked desktop refresh records determine
Ubuntu/Windows acceptance; old fixture results cannot be substituted for them.

Ember's installed package is now **`0.1.0-native-cp3-enrollment1`**, not the
older native-cp1 identifier. The signed artifact was independently rehashed
unchanged on September 13:
`f0d7c04a1478df1e96f0adc229e48b753e68371f7d4aad93c65204fa149b9842`.
The host owner reports signing and activation under the operator direct instruction
in exchange `to-voice/20260913-0218-cp3-enrollment-signed-and-installed.md`.
Its installed declarations contain:

| Control | Implemented scope |
| --- | --- |
| Speaking voice | Ten fixed named presets; default Alba; pinned for the session |
| Speaking language | English only in this native model profile |
| Recognition language | English only in this native model profile |
| Speaking pause (VAD, ms) | 320–5,000 ms; default 768; semantic handling may extend the pause |
| Speech detection (VAD) threshold | 0.05–0.95; default 0.5; VAD always enabled |
| Voice variation | Temperature 0–1; default approximately 0.3 |
| Synthesis seed | Integer 0–4,294,967,295; default 20,260,908 |

Changes apply on the next session. VAD's pause does not delay interruption.
The package also contains confirmed `speaker.enroll`, `speaker.remove` and
`speaker.reset`, plus read-only `speaker.list`; these are not TTS voice choices.
Successful live operator enrollment and a verified label reaching Ember have
**not** been independently established. The host's shared per-operation scope
still has a known concurrency/resource-lifetime defect; installed tools are not
proof that this condition is solved. Identification grants no authority.

The landed-SDK recorded-speech enrollment/recovery gates now pass on all three
desktops. Ubuntu/Windows frozen zero-argument runtime gates also pass. The new
desktop package measurements/assemblies are separate from this older signed
Mac artifact: see [desktop packaging execution](../deliverables/cp3-desktop-packages-20260913-r3/PLAN.md).
None of these results qualifies multilingual, mobile or human-level quality.

### Enrollment journey after an authorized repaired-package install

There is no dedicated enrollment button in the host flow reported by its owner.
the operator can initiate the request by asking the identity to enroll him; the identity
proposes a tool operation and the operator authorizes its exact arguments on the existing
Plugins confirmation card. Use a one-time Confirm for that proposal, not an
assumed blanket permission. The plugin never mints host confirmation metadata.

1. Keep one speech session open. Speak enough separate, normal utterances for
   the engine to finalize and embed them. The current policy needs three eligible
   samples; `speaker.list` reports the actual minimum and available final IDs.
2. The identity uses `speaker.list {}` to discover the session and eligible
   recordings. It must not infer eligibility, speaker identity, an embedding or
   a confidence score from prose or an unattributable observation.
3. The identity proposes `speaker.enroll` with the discovered session, chosen
   speaker ID/label and explicitly selected final IDs. the operator reviews and confirms
   that proposal. Caller-supplied `_host*` fields are not part of this request.
4. Pause to review if needed, but do not Finish/Close or open another session
   before confirmation completes. Continuous talking is unnecessary. The engine
   retains at most sixteen derived final-span samples for ten minutes; expiry,
   eviction or session retirement requires new recordings, not a history replay.
5. Read back the saved profile, then use held-out speech and check that the
   speaker observation naming it reaches the identity. Other speakers must remain
   unknown when they do not match. UID does not confer command authority.

An inspect-only write operation must be deliberately re-armed through the host's
existing controls before this flow. Signed private-file access, a permissible
publish target and correct per-invocation concurrency isolation are distinct
conditions; one does not prove the others. No step here authorizes bypassing the
host's confirmation, SAFE or filesystem boundaries.

### Earlier development evidence (historical, before the installation above)

2026-09-13 update: SDK `92a4265` is now cleanly landed and pinned. The complete
Mac enrollment/recovery proof passes against that unmodified SDK, and our
assembler proves eight `speech.session` controls plus four `speaker.uid`
operations with preserved confirmation flags and distinct schema hashes.
See [landed SDK proof](../deliverables/cp3-landed-sdk-20260913-r1/README.md).
The earlier isolated-patch results below retain their historical scope.
The installed-host concurrency condition is still under review: disabling
per-operation restrictions is not a substitute for invocation-owned scope.
No new signed package or operator enrollment follows from these source tests.

[Native session enrollment](../deliverables/cp3-session-enrollment-20260913/README.md)
now connects explicitly selected finalized recordings, confirmation, host CAS
publication/readback, and named subsequent observations. `speaker.list {}` is the
discovery route: current session and eligible final sequences, without embeddings.
The first real-model recorded-session path and spoken interruption/recovery
passed with an isolated SDK serializer correction. That original SDK pin dropped
the confirmation flag during Describe. Its owner has now landed the correction
and multi-interface composition; the new-pin result is linked above. Final
runtime/package freeze and resident host-operation isolation remain.
No claim here means the operator is enrolled or a new candidate is installed.

2026-09-13: the same enrollment and spoken-recovery gate now passes on
[Mac, Ubuntu and Windows](../deliverables/cp3-session-enrollment-desktops-20260913-r3/README.md).
This remains an isolated SDK-candidate result, not a signed replacement for CP3.

### Earlier native CP3 live observation (2026-09-12)

Operator-facing name was **CP3**. Its then-signed artifact retained the existing
`0.1.0-native-cp1` identifier; the name correction does not change signed bytes.
the operator reports working voice selection, barge-in and stable STT/TTS on Ember.
This is a useful live Mac checkpoint, not five-platform or human-level release
qualification. The common native profile declares ten speaking voices, English
STT/TTS, turn pause, VAD threshold, variation and seed. VAD is always active;
there is no enable/disable setting. A larger language catalog must not be
advertised until the model and adapter actually implement it.

That live Ember observation recorded `speaker_observation` on the exact spoken
turn, but had no `private:uid/enrollment.json`: its fs.read receipt
said `no such file`. Thus no enrolled name was available. The signed private
capability is present; this is not an absent operator grant. Failed reads must
remain unavailable, never an empty enrollment or an invented zero match score.

The frozen CP3 worker freshly passed two actual-SDK session readbacks containing
`session_ready.models.operator_settings`. The host's open/handle-registration
ordering can discard an early report; the host agent has the exact seam to
falsify. Do not retransmit or fabricate effective settings to mask that loss.

[CP3 follow-up evidence](../deliverables/cp3-live-followup-20260912/README.md)
records native enrollment preparation, real public-speaker recognition after
preparation, unchanged interruption/recovery, and pending operator workflow.
Source named VAD plainly and exposed `enrollment_unavailable` as a bounded
reason. Those were development changes at this earlier observation; the
subsequent signed installation is recorded at the top of this document.

## Historical CP2 observations (not the current native profile)

Status, 2026-09-11 19:11Z: the host agent reports [private Mac CP2](../deliverables/checkpoints/cp2-macos-settings-signing-20260911-r1/README.md)
signed and installed on Ember (host 70d68130), with nine declarations, two
bound reference voices, 41 recognition choices, 11 synthesis choices and
adjustable pause. The installed catalog and running process were independently
read; signed-install gate details remain the host's evidence. the operator new live
test reports playback echo transcribed as speech and self-interruption. This
fails the physical duplex boundary: the resident plugin currently has no
render-reference AEC frontend. Comprehensive language quality and integrated
UID remain unproven. Existing real selected-voice/language audio and copied-
runtime SDK gates do not override the live failure.
Earlier source observations below describe their dated starting state, not
current host restrictions. Never edit an installed signed checkpoint in place.

The [ten-voice candidate catalog](../deliverables/operator-voice-catalog-20260911-r1/resources/voices/catalog.json)
has source-bound reference recordings and separate Pocket embeddings. Acquisition
is complete; inference/listening and packaging are not qualified. It does not
expand the two-voice installed CP2 yet. Labels use documented names/roles, not
guessed accents or physical attributes. Old saved IDs must not be remapped to
different speakers when this catalog is integrated.

## Operator requirement

the operator: the plugin must expose (1) all supported speakers, (2) all supported
languages, and (3) all additional parameters the human operator can choose.

The engine owns its capabilities and validates selections. AII OS owns the
operator's persisted settings and browser presentation. Reuse the existing
plugin settings declaration and host access path; do not create a second
configuration store, a voice-only installer, or browser speech synthesis.

## What the operator sees

| Section | Choices and information |
| --- | --- |
| Speaking voice | Every usable built-in voice and explicitly added voice profile; stable ID, readable name, model binding, languages/locales, installed/available status, and a preview through the same engine. Show the selected voice. |
| Recognition language | All languages/locales supported by this recognizer and adapter; automatic detection only when implemented. Preserve distinct locale prompts; consolidate aliases without losing the actual model key. |
| Synthesis language | Independently selectable from recognition language. All languages supported by the active synthesizer; validate voice/language compatibility. Automatic selection is a mode, not a language. |
| Speaker identification | Separate from the TTS voice list: enrolled speakers, names, enrollment/verification status, enablement and explicit enrollment/removal through the existing UID ownership boundary. Unknown remains unknown. Enrollment never implicitly authorizes cloning or grants operator authority. |
| Other controls | Every implemented operator-adjustable control with type, units, default, allowed range/options, current effective value, and when a change takes effect. Explanations must identify quality/latency tradeoffs. |

Group ordinary controls before Advanced. Use searchable choices for long lists.
When a choice has a friendly label, show that label in the ordinary selector;
do not append the internal ID merely to make persistence possible. Persist and
report the stable ID unchanged, keep it searchable, and expose it with source
provenance in details. Voice descriptions must come from an actual audition or
documented source metadata, not guesses from a dataset number. Include an
explicit cancellable preview through the same synthesis/playback path.

List supported-but-not-installed voices/models with the actual installation
route; do not silently download or switch models when an option is selected.
Unsupported controls may be shown disabled with a reason, never as functioning
sliders. Exposing model support does not certify human-level quality in every
language: distinguish model-declared support, connected execution, and measured
qualification without hiding legitimate capabilities behind an English-only UI.

Additional controls to audit and expose where implemented:

- Speaking rate, pitch and style/emotion/instruction, only where the selected
  backend actually implements them. Browser volume/mute and device selection
  remain browser-owned; they are not model parameters.
- Synthesis sampling controls such as temperature, top-k, top-p, repetition
  penalty and deterministic seed, with tested bounds and accurate labels.
- Recognition latency/accuracy context choices, only for supported model/export
  geometries. Do not claim a different context works for a fixed-shape graph.
- Acoustic/semantic turn handling, pause patience and interruption enablement
  or sensitivity where wired and tested. Keep the proven defaults. Do not turn
  queue bounds, transport deadlines or safety fences into arbitrary UI knobs.
- UID enablement and enrollment/verification management. Any adjustable UID
  decision policy must identify its calibration consequences and must not
  silently convert unknown speakers into known ones.

## Source observations (read-only, 2026-09-11)

Authoritative trees were clean at host `7a66cea0658ea986ebfae53dbdac751b158f28b8`
and SDK `4c3b37362270d5c89a6460954befb5d57b95d8a4`.

1. Host `internal/pluginhost/settings.go` and kit
   `pkg/aiiospkg/settings.go` already share the signed
   `install-root/settings.json` declaration. Operator values are in
   `plugins.settings.<plugin-id>`. `settings.get` returns effective values.
   The Plugins page renders the declaration. Both validators currently cap
   each enum at **16 values** (and separately cap the number of settings at 16).
   Enum entries have strings but no separate friendly-label field.
2. The resident SDK has `Session.HostCall(ctx, ...)`. Do not assume the ordinary
   lane's package-global `Settings.Load()` is safe on resident stdio. Prove the
   resident host route and avoid blocking its ordered admission handler while
   retrieving settings.
3. Our current private CP1 author configuration declares no settings.
   `scripts/private_cp1_package.go` explicitly assembles descriptors, models,
   runtimes, accelerator data and the carrier; it does **not** package a
   settings declaration. Adding a JSON author field alone therefore does not
   add a usable UI or make the engine consume a value.
4. `runtime/speech_output/mlx_backend.py` fixes English, temperature 0.9, seed
   17 and 256 tokens per segment. `ResidentMLXModels` reuses this method.
   `runtime/voice_core/preview_stt.py` fixes `en-US` for both preview and
   confirmation. The native streaming adapter also fixes `en-US`.
5. The pinned Mac Qwen3 **Base** configuration has an empty `spk_id`: there is
   no built-in named-speaker list to copy from a CustomVoice model. Its language
   IDs cover Chinese, English, German, Italian, Portuguese, Spanish, Japanese,
   Korean, French and Russian. The library additionally implements `auto`.
   Today the engine selects English; these other choices still need wiring
   and execution proof. Display the existing unconditioned default honestly,
   not as a named, stable enrolled speaker.
6. The current MLX Qwen generation interface accepts a `speed` argument but
   documents it as not implemented. A working speed/pitch selector needs an
   actual implementation and audio proof; signature inspection is insufficient.
7. The Mac Nemotron config contains 121 prompt aliases mapping to 84 distinct
   prompt indices; its model card states 40 language-locales. **The prompt
   dictionary is not itself a claim of 84 trained languages.** Derive the
   supported set from the bound model's documented support and actual prompt
   mapping, reconcile discrepancies, and retain locale distinctions. The
   library silently falls back for unknown keys: our boundary must refuse
   unsupported values before they reach that fallback.
8. Our Windows Pocket profile binds only `embeddings/alba.safetensors` and
   disables cloning. The CUDA Qwen path binds one verified reference voice.
   A cross-platform union must not advertise these as universally installed
   options. Extra voices require their actual bound artifacts and execution.

## Implementation ownership and minimum host/kit work

Voice platform:

1. Build the complete capability inventory from the selected, verified model
   and voice artifacts plus the implemented adapter controls. Use that same
   inventory to generate declarations and validate selections; no UI-only
   hard-coded list. No model inference is needed just to enumerate metadata.
2. Package the declaration with the normal SDK packager (or explicitly call
   `SettingsJSON` in the private assembler). Its bytes must enter the package
   hash. Preserve prior checkpoint files; issue a new candidate, not an edit
   under the old signature.
3. Read and strictly validate a settings snapshot for session opening; apply
   STT choices to both preview and confirmation and TTS choices to every text
   segment. Reject unknown keys/IDs, unsupported combinations and non-finite
   numbers. Do not silently fall back to English or a default voice.
4. Keep controls nonblocking. Configuration preparation/model or voice loading
   must not block stop, cancel, Finish or Abort. A session pins its effective
   choices; a settings save must not mutate an in-flight utterance/generation.
   Report saved versus currently applied values separately. First implementation
   can apply changes on the next session, clearly labelled; no automatic
   teardown of an active conversation. Later live changes require a documented
   boundary and explicit acknowledgment.

AII OS / SDK agent:

1. Remove the 16-choice obstacle by raising the shared bounded enum limit to
   at least 256, holding kit and host to the same acceptance vectors. Keep a
   bounded total payload and rendering cost. Do not split one language choice
   into several settings or replace validation with unconstrained free text.
2. Provide readable choice labels without changing their stable values. Use a
   minimal backward-compatible extension of the existing settings declaration
   if needed, not a new voice configuration service. Search/filter must not
   silently omit choices. Keep number-of-settings limits separate from
   number-of-options limits; raise the former only if the concrete inventory
   needs it.
3. Prove the existing resident `Session.HostCall("settings.get")` route. If
   unavailable, wire it through the existing settings owner, preserving
   plugin scoping, SAFE and responsive interruption. Coordinate its exact
   result shape with the voice carrier before implementation.
4. Render desired and effective values with an honest next-session application
   label. Current generic page text says "next call", which is not enough for
   a long-lived resident session. Preserve settings across restart and reject
   invalid persisted selections explicitly on upgrade instead of quietly
   replacing the operator's choice with a default.
5. Continue the separately agreed UID host seams. Do not build a second speaker
   enrollment database to populate this page.

## Acceptance (not a documentation-only completion)

- Complete enumeration equality against the frozen supported inventory,
  including a choice beyond index 16 and the final choice; aliases do not
  inflate the count and locale distinctions are retained.
- Page save -> existing configuration owner -> resident SDK -> engine ->
  effective-value readback -> real changed inference/audio. A widget test alone
  does not prove a setting takes effect. Selection must survive reopen/restart.
- Multiple voices and non-English STT/TTS tested through the real plugin on
  each claimed backend. Include unsupported pair, missing voice asset, invalid
  numeric value and unknown language; no successful English/default fallback.
- Settings saved during synthesis leave that generation and its receipt
  accounting unchanged. The next eligible session uses the new values.
- Delayed settings retrieval and voice preparation do not hold interruption;
  opening words, recovery, complete tail, Finish and Abort retain their gates.
- Read-only enumeration neither acquires microphone access nor speaks,
  enrolls a person, downloads weights, reloads the active models or changes the
  running checkpoint. Preview is an explicit user action, cancellable through
  the same output owner, not a hidden playback path.
- No TTS voice selection changes UID identity, enrolled speakers or authority.

This requirement does not authorize signing, public publishing or changing the
operator's live test session. Qualification status stays separate from declared
model capability and from a passing settings round-trip.

## Development implementation and evidence, 2026-09-11

`runtime/plugin_engine/options.py` now derives the complete MLX catalog from
the bound model configs and model-card support: 40 recognition locales plus
auto, 10 synthesis languages plus auto, no named Base-model preset speakers.
Seven declarations include five implemented sampling controls. The same object
validates values and supplies immutable per-session STT/TTS adapter arguments.
Unknown keys/languages, non-finite values, unsupported numeric ranges and
fractional top-k/seed fail explicitly rather than selecting a fallback.

The worker now requests settings from the Go carrier on opening. The carrier
calls the existing resident `Session.HostCall("settings.get")` on one separate,
bounded reader, never the ordered control handler or worker reply reader.
Only that read operation is exposed privately; no enrollment, write access,
public operation or second settings store was added. Failed/malformed host
results fail opening. Settings are read once per open, pin each session, and
are returned in `session_ready.models.operator_settings` and raw
`status.operator_settings`. Current host typed snapshots/UI still need to
display those effective values separately from saved values.

Evidence: `deliverables/operator-settings-20260911-r3/README.md`, with 72
focused tests, whole carrier plain/race suites, four carrier build artifacts,
and framed SDK/private-worker tests using deterministic models. The exact
SDK pin remains `5c64d7c`, not a claim of a joint gate against newest main.
Two compiling in-memory mutations fail their actual assertions: ignoring the
selection and applying a retired session's settings to its successor.

The real SDK declaration probe refuses only the 41-choice recognition setting
under its existing 16-value enum limit. No limit bypass was made. The private
assembler now calls `SettingsJSON` when a declaration exists and hashes those
bytes into the normal bundle; existing settings-free signing inputs are intact.
Actual packaging of this full declaration waits for the coordinated bounded
enum change. Top-k and seed also need integer-aware UI/shared validation:
current numeric declarations document and engine-enforce whole numbers, but
the generic host number editor can accept fractional saved values.

No neural model was loaded, microphone acquired, audio played, UID enrollment
changed, package signed, identity restarted or checkpoint promoted by this work.
Real saved-choice -> changed multilingual speech and Windows/Linux backend
selection remain explicit acceptance gates, not consequences of this proof.
