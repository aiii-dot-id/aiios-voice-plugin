# Speaker attribution candidate contract

This is the beta.5 **engine implementation contract**, not evidence of host
adoption, installed behavior or a completed overlapping-speech repair. The
matching host implementation and clean speaker-specific acoustic evidence are
release requirements. Do not publish this candidate as fixed speaker ID yet.

The machine-readable declaration is
[`spec/speaker_attribution.schema.json`](../spec/speaker_attribution.schema.json).
The native producer executes the shared
[`spec/speaker_attribution_vectors.json`](../spec/speaker_attribution_vectors.json)
in `native_segment_attribution_contract`. JSON Schema describes structure;
cross-field sample extents, session ownership and idempotency also require
stateful validation. The vector file is not an installed consumer receipt.

## One final, explicit state, one terminal attribution

The existing event envelope supplies `type`, `session_id`, `sequence`, `id`
and `observed_monotonic_ns`. A `transcript_final` includes `text`, `track_id`,
`start_sample`, `end_sample` and an `attribution` object. The final's public
`sequence` is the immutable final reference; an amendment's own event sequence
is not the final reference. A conversation database number is neither of these.

The attribution object has `decision`, `reason`, `speaker`, `speaker_id`,
`revision` and `used_for_permissions:false`. Wire `known` means identified for
display; no second `identified` token is introduced. Initial revision is zero.

- `pending`: a speaker result remains outstanding. It is part of the final,
  never inferred from missing metadata or a separately scheduled annotation.
- `known`: speaker-specific matching accepted the enrolled ID and label.
- `unknown`: suitable speaker-specific evidence was compared and no enrolled
  identity was accepted. It is not evidence that a different person exists.
- `uncertain`: evidence is unavailable or unsuitable; the reason says why.

Non-known states have empty `speaker` and `speaker_id`. The current mixed-input
recognizer has an **empty `track_id`**, because it has not separated an acoustic
speaker. An input stream or a generated counter must not pretend to be that
speaker. Its pooled positive AND negative identity matches are contained as
`uncertain` / `speaker_track_unverified`, with no candidate name or raw matcher
diagnostic sent downstream. Structural refusals preserve their reason.

This containment intentionally does not restore person identification. Only
the integrated native multitalker engine can supply the required track and
clean evidence. A source-level known-state test is not that integration.

## Amendments and evidence

`speaker_observation` remains the amendment type. It carries `refers_to` (the
original final's public sequence), the exact original track and half-open
sample span, the resolved attribution fields, `revision:1`, and `late:true`.
The sample clock is the session's negotiated engine input clock. Encoder-local
frame counters are not audio sample timestamps.

The host must resolve by `(session_id, refers_to)` and check the exact track and
span before mutation. An identical terminal revision is idempotent; a conflicting
revision is refused. It never emits another final, re-executes a turn, or changes
the attribution of a successor session that reused local numbers.

That key is scoped to the host's owning plugin activation. Session IDs need not
be globally unique across process restarts: the host must reject events and
snapshots from a retired activation before looking up any session/final key.
The current worker also refuses reuse of a session ID within its own process.

A known or unknown comparison additionally includes `evidence_scope`:
`start_sample`, `end_sample`, `enrollment_revision`, `policy_sha256` and
`embedding_binding`. In this conservative first implementation the evidence
interval must lie inside that same final, and the native clean-evidence object
must carry the identical session/final/track key. Borrowing earlier-segment
identity through inferred continuity is not enabled. The acoustic evidence
owner must establish that the interval is suitable single-speaker evidence;
the attribution adapter validates ownership, not the acoustics itself.

Raw embeddings, enrollment documents and rejected candidate labels are never
consumer evidence. Similarity scores, if supplied by a qualified comparison,
are not calibrated probabilities. A voice match is never command authority.

## Bounded custody and recovery

The producer keeps at most eight outstanding model-result references and 128
recent attribution records. A pending public state becomes terminal uncertain
after 15 seconds (`speaker_match_timeout`); the associated outstanding model
reference stays retained until its caller retires, so a late return cannot
revive the identity or fault merely because recent history filled up.

Abort/failure retires pending states with `session_aborted` / `session_failed`.
An unexpectedly missing result on clean termination uses `speaker_result_missing`.
Failure payloads and session status include bounded `attributions` snapshots.
Each row carries `refers_to`, track/span and attribution fields; the enclosing
status/failure supplies session identity. These snapshots permit reconciliation
of lost observations. They are not a durable history service: the host owns
durable annotations and must not infer an absent row means a known identity.

The engine emits timeout/cancellation amendments while possible. On a faulted
lane ordinary events may be suppressed; the failure snapshot preserves their
terminal decisions. A transport loss must itself retire the host's pending
states explicitly rather than leave them pending indefinitely.

The host must preserve a single final across event/snapshot duplicates. In an
identity-restricted include/exclude stream, unresolved text must not first be
delivered and then retrospectively withdrawn. Bounded hold followed by explicit
withholding is required until the filter can decide. All-speaker delivery may
show pending without asserting a name. Host filtering and rendering remain a
separate installed acceptance gate, not a producer test result.

## Required joint tests

Pending to known, unknown and uncertain; identical duplicate amendment;
conflicting amendment; mismatched span; wrong track; stale session; reused
track/sequence after restart; simultaneous known/unresolved tracks; model timeout
followed by late success; abort/failure; event loss reconciled from status;
include/exclude filtering without duplicate turns or unauthorized delivery.

The worker proof uses real worker/C API/session transport with a held fake UID
model. It demonstrates explicit pending before model completion, pooled-match
containment, exact span/reference, snapshot reconciliation data and restart.
It does not demonstrate native acoustic separation or an installed host join.
