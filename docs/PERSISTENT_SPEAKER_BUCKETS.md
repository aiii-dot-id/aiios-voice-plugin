# Persistent anonymous speakers

Operator requirement, September 20: separation and attribution must work before
anyone supplies a name. Each distinguishable voice gets an opaque persistent
speaker UUID; its attributed segments belong to that speaker. The consuming
identity can associate a name or external person ID later, including after the
conversation ends. Live enrollment during speech is not a prerequisite.

This is a release requirement and implementation plan, not a claim that the
current worker creates these records. The current containment adapter still
has no separated acoustic tracks. Random IDs on mixed input do not satisfy it.

## Three different identities

1. A **segment** is an immutable transcript unit with session, final sequence,
   exact input-clock span and text. Amendments refer to it; they do not create
   another utterance or replay a command.
2. An **acoustic track** is the model's local stream of a distinguishable voice.
   Two overlapping voices can have overlapping spans and different tracks.
   A track number is not stable person identity and must survive neither a
   restart nor a track swap by assumption.
3. A **speaker UUID** identifies a persistent anonymous voice profile, scoped
   to the installation's private speaker store. It is randomly generated,
   never a hash of a person's voice or a cross-application tracking token.
   A human-readable label is mutable metadata attached to that UUID.

Segments link to UUIDs only through suitable speaker-specific evidence. Audio
arrival order, names spoken in the text and the loudest voice are not evidence.
A new distinguishable voice can receive a new anonymous UUID automatically;
continuing or returning speech joins an existing bucket only when the acoustic
link is supported. If continuity is ambiguous, retain an unresolved segment or
separate provisional track rather than silently merging people. Do not invent
words or a second speaker solely because an overlap detector fires.

## Durable ownership, without duplicate transcripts

The plugin's existing private store owns a versioned speaker registry: UUID,
creation/revision metadata, model-bound profile references and optional labels
or external IDs. Acoustic profiles are private data, not consumer events.
The host owns transcripts and durable segment-to-speaker annotations, as it
already owns conversation records. A bucket is that indexed relationship,
not another unbounded copy of transcript text inside the engine.

Final attribution and later corrections retain the original segment key and
their revision. A registry rename changes display lookup for that UUID; it
does not rewrite historical text, overwrite the original attribution evidence,
or re-execute the utterance. Explicitly distinguish current labels from labels
that were displayed at the time. Matching/profile revisions remain auditable.

An identity can request a label or external-ID association at any time through
the plugin's documented speaker interface. This must not require an open speech
session, retained audio finals or re-enrollment. Apply the host's existing write
and confirmation policy rather than inventing another permission system. A
name supplied by an identity is an association, not verified legal identity and
never command authority. Rename, split, merge and removal require explicit,
auditable operations; no silent merges and no automatic transcript deletion.

## Implementation order

1. Complete native streaming speaker-conditioned recognition and overlap-aware
   tracks, with stable input-clock segment spans. Preserve all resolvable
   speakers' words, not merely one selected voice.
2. Add an anonymous profile/UUID owner on the existing private-store path.
   Qualify matching, track continuity and conservative returning-speaker reuse;
   bind profiles to model and acoustic evidence versions. Establish explicit
   retention/storage limits; never keep unlimited raw audio by default.
3. Extend the reviewed final/amendment contract with persistent-speaker linkage,
   separate from labels. Update the host's initial rendering, durable joins,
   status recovery and UUID include/exclude filters together. Resolve the exact
   wire with the host before treating it as shipped. The current four-state
   named-match contract is not proof of this anonymous registry.
4. Expose list and label/associate operations with complete input/output schemas,
   examples and effects. Test naming after session closure and process restart.
   Preserve existing enrollment records; migration must not reinterpret a
   pooled legacy match as clean evidence for a new speaker bucket.
5. Qualify the actual installed consumer: two unnamed speakers alternating and
   overlapping; repeated/returning voices; later naming; label correction;
   track permutation; unequal volume; ambiguous evidence; stale/duplicate
   amendments; restart; UUID filtering; bounded resources and deletion policy.

Persistent means durable records and supported re-identification, not a promise
of perfect voice recognition. A UUID does not make uncertain acoustics certain.
No next-release readiness claim is justified until this path and the native
speaker-specific recognition path are demonstrated together.
