# Resident speaker UUID integration

Status: implemented native/SDK candidate, not a signed or installed release.

The resident recognizer now retains bounded candidate evidence independently
for each separated acoustic track. Only a selected track window reaches the
UID model. Inferred exclusive regions are selected from microphone PCM; the
whole mixture is not used, but activity estimates are not proof of purity.
The rolling window is twelve seconds, with at most four retained ten-second
candidate spans. The UID worker has eight waiting slots and one executing job;
overload emits unavailable attribution without silently borrowing an identity.
The attribution consumer also reserves space for that immediate unavailable
result, so backpressure does not fault an otherwise healthy conversation.

The model owner connects to the existing private-store broker through a private
composition callback, not an SDK extension. The fixed `uid/speakers.json` store
is separate from legacy enrollments and captures. Typed absence alone creates
an empty registry. New UUIDs are OS-random UUIDv4 values, published through exact
CAS and verified durable readback before they leave the engine. Failure to read,
publish or establish durability produces unavailable identification. No raw
audio, embeddings or profile vectors appear in public events or tool results.

## Identity-callable tools

- `speaker.buckets {}` lists UUIDs, profile availability, matching readiness, labels and the
  exact current registry revision. No microphone is required.
  A legacy singleton retains its history but is not eligible for anonymous
  recognition; `matching_reason` explains this. See [profile admission](PROFILE_ADMISSION.md).
- `speaker.associate` requires `speaker_uuid`, `registry_revision` and
  `display_label`; `external_id` is optional. It uses operator confirmation and
  can run after session close or process restart. A name is metadata, not a
  permission grant. Stale revisions refuse.
- `speaker.forget` requires the exact UUID/revision and operator confirmation.
  It removes that acoustic profile and its label history, releasing bounded
  registry capacity. It does not delete or relabel historical transcripts,
  touch another UUID or reinterpret legacy enrollment. A later observation can
  create a new UUID. There is no silent eviction.
  If `enrollment_id` is present, first remove that legacy enrollment with
  `speaker.remove`; forgetting otherwise refuses instead of letting an active
  enrollment silently mint a replacement UUID.
- `speaker.link` corrects two independently verified same-person UUIDs after
  exact-argument operator confirmation. It requires `speaker_uuid`,
  `target_uuid`, and `registry_revision`. Future accepted source matches resolve
  to the target; profiles, matching criteria and historical words are unchanged.
  Self-target undoes the correction. See [Speaker continuity](SPEAKER_CONTINUITY.md)
  for limits and why this is not an automatic recognition repair.

All ten speaker operations, their examples/effects/confirmation declarations,
and all twelve referenced schema files are retained by the package assembler.
Complete lists have a one-MiB declared response budget, enough for the registry's
256 entries with maximal UTF-8 labels. Registry storage is capped at eight MiB
and 1,024 combined association/correction-history entries.

## Consumer contract

Each final retains its session, final sequence, acoustic track and sample span.
An exact-key `speaker_observation` and status reconciliation can carry:

- `speaker_uuid`: persistent bucket key, never the local track number;
- `registry_revision`: positive exact decimal string;
- `continuity`: `new_profile` or `matched` for emitted reusable identities;
- `display_label`: mutable presentation metadata; `unknown` when unassigned.

For anonymous matches, `speaker_id` remains empty and named-person `decision`
is `uncertain`. Enrolled matches carry **both** their legacy `speaker_id` and
the persistent `speaker_uuid`, with `decision=known`, `continuity=matched` and
`display_label`. Their evidence scope binds both enrollment and registry
revisions. The UUID is the common person key; neither a track number nor a name
is substituted for it. Neither path asserts authentication.
Missing evidence or ambiguity emits no new UUID or registry mutation. Such a
final remains discoverable by its session/final/track reference; scored ambiguity
still carries diagnostics. Older stored provisional rows remain readable and
nameable, not automatically removed or reinterpreted as recognized people.
`used_for_permissions` remains false. A host must consume the UUID fields for
live and stored annotations and allow/ignore filtering; gating them on
`decision=known` would discard the feature. Filtering unresolved speech must
not pass it through an allow-list or try to undo an already executed command.

Unknown speech now also requires corroboration before a permanent UUID is
created. The first observation returns `speaker_profile_pending` and no UUID;
see [Profile admission](PROFILE_ADMISSION.md) for independent-utterance checks,
transient bounds, replay handling and remaining acoustic limitations.

## Enrolled UUID continuity

On a legacy enrollment's first accepted match, the existing registry stores an
OS-random UUID and a reference to the enrollment ID and original recording
digests. It does not copy embeddings, modify enrollment, or derive identity from
a name, transcript or voice hash. Publication uses the existing compare-and-swap,
durable receipt and exact readback. No UUID is emitted when that write fails.
Retries and process restarts read the same binding; there is no time expiry.
Changing a label or adding enrollment recordings preserves it.

If all recordings of a corroborated anonymous profile are exactly among the
selected enrollment's evidence, binding retains that existing UUID. A merely
similar vector is not enough to merge identities. Reusing an enrollment ID with
different original evidence refuses; it cannot hand the old UUID to a new person.
An absent enrollment leaves its UUID metadata readable but does not provide an
active voiceprint. Names assigned with `speaker.associate` take precedence over
the legacy label, including clearing a label to `unknown`.

Simultaneous tracks share a canonical-UUID exclusion check across both named
and anonymous paths. A second track cannot claim the same UUID in the same
utterance. The final/track/sample binding still rejects whole-mixture identity;
uncertain separation emits unresolved evidence, not a guessed shared person.
These are invariants, not proof that the acoustic model never makes an error.

Compatibility: older native workers cannot read the new optional `enrollment`
binding on registry buckets. Do not downgrade a migrated live registry to an
older worker. Keep a pre-migration backup for rollback; never overwrite newer
speaker history silently. Host filters must support UUIDs while deliberately
handling pre-existing legacy enrolled-ID selections. The host owns that
compatibility and prompt/history rendering; no generic SDK change is needed.

## Evidence and limits

The real-model Mac recorded-speech SDK panel exercises seven arrangements of
two recordings: solo voices, alternating speech, equal and unequal overlapping
speech, and overlap without prior isolated speech. Both recognized streams are
preserved. Anchored overlap retains distinct UUIDs. Earlier runs minted provisional
UUIDs for cold overlap; current acceptance instead requires no UUID and no durable
registry growth for those unresolved tracks. This is engineering evidence,
not broad speaker-identification accuracy or seven independent conversations.

The test broker simulates host filesystem receipts. The actual carrier and
model worker retire and restart; only the broker's stored document survives.
The new process lists the same UUIDs, accepts a later name with speech closed,
and acoustically matches the returning recorded voice to that UUID and name.
The suite also exercises STT, TTS, VAD, interruption, recovery and Finish.

Final resident test `sdk-resident-registry-macos-r4` passed in 122.89 seconds,
including confirmed forgetting after speech closed and clean process retirement.
Its result SHA-256 is
`a926072ec8325a33a473f8577d66e13a3ddcbd64f94a622c39e0c0e7fde1bc55`.
The exact Mac runtime manifest is
`a90147da8299ddb35154774a4ce2e70f50d79e0f111edfb67af4649d705f4163`;
the carrier is
`472b0b8155d2b10d48a7ef6b8647670bc30f5e423175b64283b0c3e3e7d02c16`.
These are candidate execution bindings, not distribution signatures.

Source closeout passed 360 tests with zero skips. The real SDK package suite
passed 24 tests; the Go carrier suite passed under the race detector. Native
contract suites passed 36 tests on macOS, 37 on Linux and 38 on Windows; the
three model-free hearing/evidence contracts passed on each platform. Linux and
Windows contract results do not qualify real changed-model runtime performance.
Earlier failed attempts remain preserved, including a stale carrier detected
during schema edits and a harness-input change detected during an earlier run.

Production-store tests cover denied reads, stale CAS, missing durability,
readback mismatch, legacy-store isolation, later naming, confirmed forgetting
and recovery from the registry capacity limit. Package tests use the real SDK
assembler and assert complete schema bytes, not just descriptor names.

Installed/browser UUID joins and filters, changed-model desktop performance,
final signatures, final-byte endurance, downloadable-asset acquisition and
catalog promotion remain release gates. No mobile or NPU qualification is
implied. The current hearing profile supports English; unsupported languages
must not be advertised as selectable capabilities.
