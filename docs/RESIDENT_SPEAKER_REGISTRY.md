# Resident anonymous-speaker integration

Status: implemented native/SDK candidate, not a signed or installed release.

The resident recognizer now retains bounded candidate evidence independently
for each separated acoustic track. Only a selected track window reaches the
UID model. The mixed microphone recording is never used as a speaker profile.
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

- `speaker.buckets {}` lists UUIDs, profile availability, optional labels and the
  exact current registry revision. No microphone is required.
- `speaker.associate` requires `speaker_uuid`, `registry_revision` and
  `display_label`; `external_id` is optional. It uses operator confirmation and
  can run after session close or process restart. A name is metadata, not a
  permission grant. Stale revisions refuse.
- `speaker.forget` requires the exact UUID/revision and operator confirmation.
  It removes that acoustic profile and its label history, releasing bounded
  registry capacity. It does not delete or relabel historical transcripts,
  touch another UUID or reinterpret legacy enrollment. A later observation can
  create a new UUID. There is no silent eviction.

All nine speaker operations, their examples/effects/confirmation declarations,
and all eleven referenced schema files are retained by the package assembler.
Complete lists have a one-MiB declared response budget, enough for the registry's
256 entries with maximal UTF-8 labels. Registry storage is capped at eight MiB
and 1,024 association-history entries.

## Consumer contract

Each final retains its session, final sequence, acoustic track and sample span.
An exact-key `speaker_observation` and status reconciliation can carry:

- `speaker_uuid`: persistent bucket key, never the local track number;
- `registry_revision`: positive exact decimal string;
- `continuity`: `new_profile`, `matched` or `provisional`;
- `display_label`: optional mutable presentation metadata.

Existing `speaker_id` remains empty and named-person `decision` is `uncertain`.
These fields distinguish acoustic continuity from named enrollment/authentication.
`used_for_permissions` remains false. A host must consume the UUID fields for
live and stored annotations and allow/ignore filtering; gating them on
`decision=known` would discard the feature. Filtering unresolved speech must
not pass it through an allow-list or try to undo an already executed command.

## Evidence and limits

The real-model Mac recorded-speech SDK panel exercises seven arrangements of
two recordings: solo voices, alternating speech, equal and unequal overlapping
speech, and overlap without prior isolated speech. Both recognized streams are
preserved. Anchored overlap retains distinct UUIDs; cold overlap yields distinct
provisional UUIDs, not a guessed enrolled person. This is engineering evidence,
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
