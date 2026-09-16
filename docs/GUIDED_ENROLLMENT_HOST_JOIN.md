# Guided enrollment: executable engine candidate and host join

2026-09-16. This is the implemented engine contract in signed desktop
integration candidate `e1d873140eb94b46…`, not an agreed new SDK-wide API or
an installed host UI. It extends the voice engine's open
arguments over the existing two audio/control planes. Ordinary speech open,
settings, synthesis, interruption and playback receipts keep their behavior.

## Human journey and ownership

The human chooses Add speaker (an AI may request that the human do so), makes
one explicitly consented recording, and stops. The engine prepares a private
embedding and returns a stable pending capture handle. Later, even after the
microphone closes and the plugin restarts, the AI can list that handle and
propose enrollment with a name. The host confirms those exact arguments before
dispatch. The engine publishes the profile before retiring pending evidence.

The engine now implements capture/retention/confirmation; the host UI and its
mode selection still need integration. The host must never enter this mode
from ambient listening, model prose or an unconfirmed enrollment proposal.
Host-owned consent, private-file grants and operator confirmation remain the
authorities. Voice matching never grants command permission.

## Open and capture over the existing plane

Close an ordinary speech session before entering capture mode. Reuse the
existing host-owned native audio plane and `speech.session.open`, adding:

```json
"enrollment_capture": {
  "consented": true,
  "request_id": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "created_ms": 1789500000000
}
```

The sample values above are illustrative: the **host** supplies a fresh
64-lowercase-hex request nonce and trusted positive UTC milliseconds at actual
consented capture. This is an exact three-field object; no audio, path, speaker
label or embedding is accepted here. Missing/false consent, malformed metadata
and a runtime without the separately bound one-recording policy refuse open.
No SDK method or unsolicited event type was added for this mode.

Normal `session_id`, input/output handles and explicit audio descriptors are
still required. The engine acknowledges confirmed **16 kHz mono s16le input**
and 24 kHz mono output. This mode never synthesizes output. The host remains
responsible for converting the browser stream to the confirmed engine clock.
It must not feed the source clock or guess a conversion rate.

Open acknowledges admission and emits `session_ready`; no ordinary speech
session or settings lookup is created. The response and status identify
`purpose: "enrollment_capture"`. Capture PCM does not enter STT, conversation,
transcript history, normal VAD segmentation or TTS.

The recording must contain 31,920–480,000 complete engine-clock samples
(1.995–30 seconds). This is an extent bound, not a claim that two seconds are
always sufficient for identification. Guide a natural substantial recording;
silence/invalid model evidence is refused rather than made into an enrollment.
The host should stop at the supported maximum. The engine never silently pads,
crops, evicts or turns part of an oversized recording into a successful one.

## Stop, completion and cancellation

Stop disables capture at the browser, flushes its conversion tail, sends the
existing exact `speech.session.finish_input` cutoff and audio END. Control and
audio can arrive in either order; they must name the same exact end. The first
Finish admits the cutoff, not completion. The declared final tail must arrive
within two seconds. A contradictory cutoff, gap, discontinuity or extra PCM
is refused/faulted, never hidden by trimming.

Preparation and private-file CAS/readback run on a background owner. Status
and abort do not wait behind inference or storage. `enrollment_capture.state`
is `preparing`, then `retained` only after durable publication and exact
readback. The retained result includes `capture_id`, `samples`, `created_ms`
and publication evidence, not the vector or recording.

`input_finished` follows completed preparation/retention and names the exact
input handle and end sample. `close(mode="drain")` may be admitted after
Finish and waits for this result. The ordinary `session_end` carries the same
capture result and zero model padding. The host must display capture readiness,
not invent a final transcript or conversational response on this completion.

`close(mode="abort")` fences preparation/publication and retires the owned
recording. Inference may finish in the background, but cancelled work cannot
start a new broker publication. A publication already sent at cancellation
can be unresolved; do not assert that nothing changed. List pending captures
to reconcile. Completion/abort drops held input buffers. No secure heap-erasure
or power-loss guarantee is inferred from the in-process fixture.

## AI-callable management, including after restart

`speaker.list {}` works without a session ID or open microphone. On the guided
policy it reports `guided_capture_available`, `pending_captures` (opaque ID,
creation time and sample count), enrolled speakers and current session state.
Pending capture capacity is 16 / 64 KiB: no timer, silent eviction or ambient
conversation recording. While capture is active, management asks to finish
and close it first; normal speech retains its existing management semantics.

The new preferred enrollment form is:

```json
{"capture_id":"COPY_THE_EXACT_LISTED_HANDLE","speaker_id":"sam","label":"the operator"}
```

The placeholder in this explanatory block must be replaced. The shipped JSON
example uses a syntactically valid illustrative hash; that is not a default or
an existing capture. Required arguments and their meaning are in the packaged
schema and descriptor, not solely in this document. The host adds its own
invocation time and exact operator-confirmation stamp; callers cannot supply
those as authority. `capture_id` and legacy `finals` are mutually exclusive,
enforced by admission and the worker. The SDK's closed schema subset lacks
`oneOf`; no unsupported keyword is shipped to imply validation it cannot do.

`speaker.discard_capture {"capture_id":"COPY_THE_EXACT_LISTED_HANDLE"}`
discards pending evidence only, under the same operator-confirmation rule.
It does not remove an enrolled speaker. `speaker.remove` / `speaker.reset`
retain their existing enrolled-profile meanings.

Enrollment returns separate `enrollment_durable` and
`capture_retirement_durable` facts. A profile that committed while cleanup
failed is not reported as unchanged. Explicit reconciliation cannot relabel
already enrolled evidence and re-attests the identical profile before cleanup.
Do not automatically replay an uncertain operation. Listing gives the concrete
next action instead of requiring the identity to race a live speech session.

## Compatibility and release gates

No deployed profile, old policy, package, signature or identity was changed.
The existing live-final path is retained for unchanged checkpoints. Guided
capture requires an explicitly bound one-recording policy; an old three-sample
snapshot is **not silently reinterpreted**. The current native candidate also
supports an immutable `uid_previous_policy` path in `native-profile.json`.
The runtime verifies both policy assets; a profile can select only one of
those bound policies, never supply new thresholds. Existing profiles continue
to identify speakers under their original policy before any upgrade. A
two-recording incomplete profile remains unready under its three-recording
policy. Loading the runtime does not write or transform a profile.

`speaker.list` reports `policy_sha256` (effective stored policy),
`runtime_policy_sha256` (new guided policy) and `policy_upgrade_required`.
When the latter is true, close speech and propose `speaker.upgrade_policy {}`.
Its shipped input schema and descriptor require no caller-supplied policy,
model path, threshold or vector. The host confirms the operation. The engine
refuses an incomplete old profile by speaker ID, preserving it for explicit
operator review; existing remove/reset and live-final additions still use the
stored policy. Do not silently remove an incomplete speaker to force upgrade.

Upgrade changes only the bound policy and one revision, preserving every
speaker ID, label, recording digest and embedding bit. The ordinary private
file owner performs CAS, publication and exact readback. An unsynced result
is failed/unresolved even when renamed bytes are readable. Explicitly repeating
the confirmed operation re-publishes the identical current bytes and verifies
durability without advancing the revision a second time. The real SDK test
includes process restart and known/unknown recorded-speech decisions before
and after this transition. This is not a physical power-loss test.

Before catalog publication: join this mode to the host's consented UI, perform
the profile-transition UI journey, implement only/ignore UID filtering before
all transcript fan-out, then repeat contained fresh-cache desktop install and
live journeys on final companions. The desktop integration archive is already
T3-signed under the operator authorization; that signature does not close the host
integration or installed-journey gates. Exact bytes and remaining publication
requirements are in `deliverables/beta1-signed-publication-20260916-r1/README.md`.
No model artifact or cloud provider is required to implement this host mode;
the native engine owns its local biometric evidence and the host owns delivery.

## Stable UID observations (2026-09-16 candidate)

The native `speaker_observation` now exposes `speaker_id` beside the existing
`speaker` display label. A known result carries the exact enrolled ID returned
by `speaker.list`; unknown/uncertain results carry an empty ID. Rename and two
people sharing a label must not change filter identity. The host must never
infer an ID from a label or from the engine-specific `native_evidence` object.
An older event without `speaker_id` cannot satisfy a known-ID filter.

This additive field travels through the existing SDK event notification. It
does not authorize any action or change `refers_to`, session ownership, or the
host's obligation to withhold restricted text before page/history/steering.
Host adoption and installed filter qualification remain open; this engine
change alone does not implement the requested all/only/ignore behavior.
