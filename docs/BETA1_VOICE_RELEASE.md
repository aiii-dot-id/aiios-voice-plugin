# AII OS Voice Plugin — Beta 1 delivery contract

Operator direction, 2026-09-15: Go AII OS is already Beta 1. Deliver its
downloadable voice plugin to Beta 1 testers, not another private-engine
checkpoint. The operator also rejected live-session-dependent, mandatory
multiple-recording enrollment and requires UID include/exclude filtering.

## Deliverable

One `id.aiii.voice` package selecting native Windows x86_64, Ubuntu x86_64
(24.04 and 26.04), and macOS arm64 companions and model data through the
existing Plugin SDK declarations. It installs without a source checkout or
system Python. The existing host owns download/resume/hash verification,
installation, grants, audio devices, updates and rollback. No second installer.

Beta 1 must provide stable selectable TTS voices, truthful language choices,
STT, active VAD, an adjustable pause, barge-in with opening words retained,
recovery, UID enrollment/removal/readback, and UID-controlled transcript delivery.

Keep the measured working engines. New training, new model architectures,
mobile qualification and further optimization are not prerequisites for this
desktop release. English-only language support, ten voices, hardware
requirements and measured cold-start times are documented Beta 1 limits, not
reasons to withhold a working release. Do not advertise unimplemented languages.

## UID usability is part of this release

The initial CP3 package had these UID usability defects (retained here as the
regression checklist, not a description of the updated source):

- `plugin/native/enrollment.go` requires `session_id` and routes operations
  only through the resident speech lane.
- `runtime/native/session/worker.cpp` and `c_api.cpp` require an open session
  for discovery/enrollment; even remove/reset inherit the worker's live gate.
- `runtime/native_uid/session_evidence.h` clears candidates on cancellation
  and new open. Session-local final numbers are not durable recording handles.
- `runtime/native_uid/identity.cpp` rejects a profile with fewer than its
  policy's sample count; current policy uses three. Merely changing three to
  one would not establish one-recording recognition quality.

Source update on 2026-09-15: listing, removal and reset have been separated
from live audio in the candidate; their schemas make `session_id` optional
and list reports `session_open`. Existing signed packages are unchanged.
The guided native capture/closed-microphone confirmation path now executes
through the real SDK and survives process restart; the old final-reference
path remains supported for existing clients. A confirmed policy-upgrade
operation preserves existing usable speaker evidence and matching decisions,
while an older incomplete profile remains unready until explicitly resolved.
See `GUIDED_ENROLLMENT_HOST_JOIN.md` for the executable contract. Host capture
UI, only/ignore delivery filtering, final packaging and installed qualification
are still release work, not implied by these component results.
Standing operator confirmations use the SDK's repeatable `auto`
stamp; only one-use act IDs are consumed by the replay guard.

Replace that UX with an explicit enrollment workflow:

1. Human selects **Add speaker**, names the person and starts capture. An AI
   may propose the same action; the human controls consent and confirmation.
2. One guided recording captures enough usable speech. The human stops; no
   concurrent AI tool call or continued speaking is needed.
3. Quality/readiness is reported. Ask for additional speech only with a reason
   (for example insufficient usable speech or conflicting speaker evidence),
   not because an arbitrary count of separate recordings was not reached.
4. A pending, explicitly requested enrollment sample remains selectable after
   microphone stop/session close and application restart, until confirmation
   or discard. Persist only the needed private embedding/evidence, not ambient
   conversation audio. Bound storage; refuse new capture when full rather than
   silently evict a sample an operator is reviewing. This does not authorize
   retaining every conversation as an enrollment recording.
5. Confirmation binds the chosen UID, label and exact sample handle/digest.
   Publish with the existing private-file CAS/durability/readback owner. A
   changed selection requires a new confirmation. Clear the pending candidate
   after a confirmed durable commit; preserve unresolved publication for
   reconciliation, never automatic duplicate enrollment.
6. List, rename where supported, add more evidence, remove and reset must not
   require a live microphone. Ordinary speech session close does not erase
   enrolled profiles or pending explicit-enrollment evidence.

One recording is one human capture operation. Internal processing may analyze
multiple portions but must not label correlated windows as independent
recordings or reuse enrollment audio as held-out identification evidence.
Single-recording acceptance requires held-out known/unknown/confusable tests;
uncertain matches remain uncertain. UID grants no command authority.

## UID transcript filtering

Expose the same effective policy to the human UI and AI-callable plugin tools.
Use one owner and one readback, not independent UI and AI policy copies.
The intended operation below is an engine/plugin contract proposal, not a
claim that these names are already in the released SDK:

```json
{"mode":"only","uids":["uid-sam","uid-ada"]}
```

```json
{"mode":"ignore","uids":["uid-visitor"],"unidentified":"deliver"}
```

| Mode | Matched listed UID | Matched other UID | Unknown/ambiguous/unavailable |
| --- | --- | --- | --- |
| `all` | Deliver | Deliver | Deliver with honest attribution |
| `only` | Deliver | Withhold | Withhold |
| `ignore`, unidentified=`deliver` | Withhold | Deliver | Deliver, explicitly unidentified |
| `ignore`, unidentified=`withhold` | Withhold | Deliver | Withhold |

`all` takes an empty list. `only` with an empty list deliberately delivers no
speech. `ignore` with an empty list ignores no known UID and still applies its
explicit unidentified rule. UID values are stable enrolled IDs, not names or
model guesses. Reject unknown/duplicate/malformed IDs and contradictory fields
atomically, preserving the old effective policy. Do not silently degrade an
allow-list to `all`. Removing an included UID must never broaden the filter.

Filtering precedes transcript delivery, logging as conversational text,
`rememberFinal`, `handleHeard`, history/steering and LLM context. Current host
`voiceEngineEvent` fans the final and starts `handleHeard` before a later
speaker observation arrives; filtering only that later observation is too late.
Likewise current telemetry fans partials directly. Restricted modes must not
leak partial text before classification. A full-utterance UID engine can hold
partials/finals until the corresponding final decision is available; it must
not pretend to offer zero-delay speaker-specific partials.

Bind the decision to the originating activation/session, utterance/final and
filter revision. Delayed decisions must not release stale text after a policy
change, reset, abort or session replacement. Apply restrictions immediately to
undelivered text when acknowledging a policy change; no already-delivered text
can be retracted. Bounded withheld-text custody must participate in Finish
completion. Every utterance gets exactly one admitted/withheld outcome; withheld
utterances produce no reply and are not replayed when the policy is relaxed.
Expose filter mode/revision, suppressed counts and reasons without suppressed
transcript contents. Operator enrollment capture is a separate explicit action,
so an empty allow-list cannot prevent enrolling the first person.

This controls which STT the AI receives. It does not grant permissions, replace
host authentication, or implicitly alter microphone capture, VAD, playback-stop
and barge-in semantics. Any request to make interruption speaker-selective is
a separate behavior decision. Preserve the host's existing authority rules.

## AI-visible contracts are release functionality

An operation name alone is not a usable capability. For every AI-callable
speaker operation, the shipped descriptor must include a useful summary,
search terms, JSON argument examples and package-contained input/output
schemas. Every parameter needs its type, meaning, required/optional status,
limits, and the discovery operation that produces any referenced UID or
capture handle. Use stable IDs, not labels guessed from speech. The examples
must validate against the actual host schema validator. Confirmation stamps,
embedding vectors, raw audio and private storage paths are not AI arguments.

Discovery must also state lifecycle requirements and the concrete next action
on unavailable data. Results expose the effective state and publication
readback, not merely success text. Host-driven speech lifecycle controls are
not AI tools; explain the operator/UI route instead of advertising an empty
or callable-looking stub. Do not declare filter or after-capture operations
before their producing and execution paths exist.

The assembler now refuses missing speaker summaries/schemas/examples,
undocumented parameter types and missing/invented example keys. The focused
host probe loads the built archive with `loadDescriptors`, examines the same
`Parameters` and `Discovery` objects exposed to the AI, and runs the published
examples through that host's validator. This is schema/discovery evidence,
not a substitute for signed activation or successful enrollment.

Before release, a fresh identity must discover the operations without private
instructions, inspect the contract, list available speakers/captures, propose
a valid enrollment or filter, receive the operator's confirmation where
required, and verify effective state. Missing-schema and invalid-argument
falsifiers must fail. No binary-string inspection or human-supplied JSON
repair may be needed for this journey.

## Release execution and acceptance

1. Complete the UID workflow/filter contract jointly with the host owner and
   implement it end to end, including declared schemas and on-page controls.
2. Close actual execution failures: Ubuntu 24.04's fatal semantic endpoint
   timeout and reliable dependency acquisition. Do not relabel a crash or
   broken download as a Beta 1 limitation. Existing working Windows contained
   CPU/Vulkan and Mac Metal paths remain the starting release paths; DirectML
   improvement is not a prerequisite unless it wins and passes that same gate.
3. Assemble exact platform assets, notices, prerequisites, startup/resource
   settings, model URLs/hashes/sizes and catalog metadata. Integrate SDK archive
   binding fix `214eb463`; never derive the catalog from a different archive.
4. Run each complete install/journey against the candidate: empty-cache
   download and interrupted resume, selected platform only, integrity refusal,
   normal-user contained activation, settings saved/effective, chosen voice,
   enrollment after capture close and after restart, known/unknown filtering,
   VAD pause, multiple turns, interruption/recovery, Finish, Abort and reopen.
   The Windows VM is the Windows qualification target. Preserve prior installs
   and profiles; no silent driver or global system-policy changes.
5. Freeze the candidate and execute the existing signing ceremony against the
   exact final payload. the operator authorized the agent to perform all required
   signing on 2026-09-16; approval is no longer a blocker. Generate final catalog
   rows from the signed archive. Prepare GitHub assets, installation/update/
   rollback instructions, measured platform results and known limitations.
6. Publicly upload only with the operator's publication authorization, then
   independently read back the downloadable bytes and repeat clean acquisition.

Completion is a downloadable, signed, installed-and-exercised Beta 1 plugin
for those three desktops with these features. It is not universal human-level
quality certification, and it is not five-platform/mobile completion.

## Ubuntu 24.04 qualification environment

Operator clarification, 2026-09-15: the Linux laptop is a laptop running NoMachine and
other GPU-using applications. Qualify the ordinary shared-machine workload;
do not close those applications or change drivers, power policy, affinity,
priority or audio routing to manufacture a pass. Record concurrent CPU/memory
pressure, GPU utilization/memory and endpoint timings alongside each run.
Aggregate GPU utilization includes the voice engine itself and cannot assign
blame to another application. Report measured shared-laptop latency directly,
without an assumed correction to dedicated-machine performance. A missed
deadline or session failure remains a product finding to diagnose, not an
excuse based on the laptop's normal use. Controlled additional-load tests must
be bounded and separately labelled; they cannot replace the ordinary-use gate.

## Current guided UID implementation checkpoint

The native producer, pending persistence and closed-microphone confirmation now
execute through the actual Go SDK, including a fresh carrier/worker restart.
`docs/GUIDED_ENROLLMENT_HOST_JOIN.md` is the exact candidate host contract;
`deliverables/beta1-guided-capture-sdk-20260915-r5` binds its evidence. No new
SDK transport/event family is needed. The host consented capture UI, explicit
transition UI journey and common pre-delivery UID filtering remain unjoined.
The engine-side confirmed transition now executes through the real SDK with
existing-policy classification preserved until confirmation; see
`deliverables/beta1-uid-policy-upgrade-20260915-r4/README.md`. Do not replace a
policy asset alone or infer release readiness from component/fixture evidence.
