# Anonymous speaker implementation boundary

The native UID module now has a bounded anonymous registry codec and decision
functions. The [resident worker connection](RESIDENT_SPEAKER_REGISTRY.md) now
uses its existing private-store broker. The host consumer and installed release
qualification remain open. It is not a released plugin capability.

## Implemented

- Canonical random UUIDv4 bucket identifiers, separate from acoustic track
  numbers and optional display labels. The composition supplies OS-generated
  randomness; names, text and voice hashes never derive an identifier.
- Existing exact model/policy-bound UID decisions for reusable profiles. No
  automatic profile adaptation, silent merge or reinterpretation of legacy
  enrollment documents. The separately bound single-recording policy is
  required; the code does not lower a policy's sample floor.
- A provisional bucket when speaker-specific evidence is unavailable or a
  match is ambiguous. It is not a named person or an authentication claim.
- Metadata-only association after session close and process restart, exact
  expected revisions, idempotent unchanged associations, retained association
  history and rejection of stale, foreign or malformed requests.
- Bounded storage: 256 buckets, 1,024 association-history entries, 8 MiB. The
  registry stores model-bound profiles and metadata, never transcripts or raw
  audio. At capacity it refuses new data; there is no silent eviction. The
  confirmed `speaker.forget` operation removes one exact UUID's profile and
  metadata under revision/CAS protection, without altering transcripts.

The registry functions prepare candidate bytes and a base digest. They do not
perform durable storage. The resident owner now publishes them using host-owned
CAS and verified durable readback before emitting a UUID. Real-host filesystem
qualification remains separate from the simulated test host's receipts.

## Measured acoustic path

The native diarizer exposes bounded per-frame activity alongside its separate
speaker transcripts. An evidence selector chooses candidate contiguous windows
with strong target activity and no competing activity above its exclusion
threshold. It permits internal silence, requires two seconds of active context,
guards both boundaries, and caps each window at ten seconds. Activity is not
mathematical proof of an isolated waveform; broader acoustic qualification is
still necessary.

The first selector exposed a real mistake: extending a window through trailing
silence until another speaker crossed threshold could include that speaker's
opening audio. The corrected selector ends at the target's last strong activity,
then applies its guard. A permanent regression checks that exact case. It also
accounts for terminal truncation without overstating active context.

`prove_speaker_registry_audio.py` connects the selected recorded PCM to the real
native UID model, then the production registry functions in a fresh process for
each operation. Reference labels score the results afterward; they do not pick
windows, create identities or drive matching. Embeddings remain in the private
test process and are not printed in results.

On the frozen two-recording, seven-case engineering panel, the native recognizer
retained exact parity for all 405 reference tokens. Six cases supplied usable
profile evidence and retained the correct anonymous UUIDs across process
restarts. Post-session labeling passed. Cold overlap retained both transcripts
but had no isolated profile evidence: its two buckets remain provisional. The
result explicitly reports incomplete profile coverage. These recordings are
not seven independent conversations or broad biometric-accuracy evidence.

## Product connection

1. Implemented: bounded speaker-specific PCM from recognition into the UID
   owner, never the pooled capture as clean evidence.
2. Implemented: registry publication through the existing private-store broker,
   typed absence, exact CAS, cancellation and verified durable readback;
   list/associate/forget operations with complete packaged schemas.
3. Bind UUID/revision/continuity to each original final and the host's live,
   stored and recovered consumer views. Join/filter on the UUID; a track index
   or a nearest named person is not a substitute.
4. Run installed multi-speaker, restart, later-label and filter journeys on the
   final signed desktop artifacts before updating the release/catalog.

The resident SDK checkpoint now reports persistent UUID/revision/continuity
fields on exact-key observations. The named-person `decision` remains uncertain:
an anonymous acoustic UUID or chosen label is not verified personal identity.
The host must consume the new fields rather than coercing them into `speaker_id`.
