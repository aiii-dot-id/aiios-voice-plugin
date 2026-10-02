# Evidence selection and private recording repairs

These are source-level repairs, not a new installed or released plugin.
No identity thresholds, microphone policy, SDK permissions or live speaker
profiles change.

## Evidence cannot be starved by a sparse prefix

The bounded collector compares the active speech in a newly completed region
with the evidence already retained plus what can safely fit. It replaces the
old selection when retaining the new region supplies more active evidence.
Silence cannot permanently occupy the entire allowance and block a later
clean sentence. Original-clock regions, exclusivity checks, boundary guards
and the ten-second capacity remain intact.

`multitalker_speaker_evidence_contracts` covers a sparse ten-second prefix
followed by clean speech, preservation of stronger retained evidence, multiple
tracks, uncertain activity, overlap, expiry and memory bounds.

## Recording and UID storage share a cancellable owner

An automatic speaker read and an asynchronous waveform save can overlap.
The snapshot bridge now waits for its existing owner within the caller's
existing deadline instead of immediately refusing a valid second operation.
Read and publication retain their ten- and thirty-second total deadlines;
waiting does not add another timeout allowance. Cancellation wakes both owner
and waiters. An old waiter cannot enter a reopened session, even when its name
is reused. Explicit speaker management also checks an outstanding waveform
publication, matching the recording side's existing exclusion.

Only background storage work waits. The control reader and interruption path
do not acquire this owner. Publication still requires generation comparison,
a host receipt and exact readback; unresolved publication is not auto-retried.

`native_uid_snapshot_bridge_contract` exercises both recording/UID orderings,
failure of either first owner, cancellation, session reuse and exactly one
verified publication. The waveform fixture separately exercises the worker,
carrier and private broker path; it is not physical-audio qualification.

## A bounded listing must not prevent exact deletion

The broker returns at most 1,024 directory entries. `recording.list` now
exposes its `truncated` result. Exact-ID deletion checks the canonical target
through a one-byte private regular-file read and no longer depends on listing
the directory. That byte never reaches the tool result. Host path, file and
symlink checks remain in force.

The listing operation declares a 128 KiB result allowance: 1,024 canonical
identifiers plus their sizes do not fit in the previous 64 KiB allowance.
The capacity regression checks the actual encoded result against the callable
descriptor, not only the store helper's return value.

Pruning can remove eligible stale stages in the returned page and reports
`truncated` when that page was incomplete. It never claims to have inspected
unlisted entries or deletes completed recordings. This is bounded recovery,
not directory pagination; saved recording IDs remain usable even when absent
from the visible page. See [recording usage](ON_DEMAND_WAVEFORM.md).

`TestRecordingRecoveryBeyondHostListingLimit` creates 1,025 broker-shaped
entries, deletes a known ID beyond the visible page without listing, verifies
the next listing is complete, and refuses directory/symlink targets.

## The production worker must compile on every desktop

The native source matrix now compiles the model-free worker fixture on all
three desktops, not only Linux. This exposed an MSVC warnings-as-errors failure
in the waveform window's integer zero fill. The fill now uses its actual PCM16
element type. Compiler warnings remain enabled; no Windows test is excluded.

## Unresolved speaker-identity acceptance

These repairs do not close the two acoustic gaps:

- Continuous overlap has no isolated microphone evidence. Repeated short
  utterances cannot safely be joined merely because their model track numbers
  match. A qualified separated-waveform or acoustic-continuity path is needed.
- Current transcripts have conservative whole-utterance track extents. A
  clean subset is not independent evidence that every word in a longer track
  belongs to the same person. Known-prefix/other-speaker continuation, quiet
  intrusion and sustained overlap require end-to-end attribution tests.

Missing identity, wrong identity and transcription errors remain separate
measurements. Neither source-contract success nor improved waveform separation
alone qualifies durable mixed-speaker identity. No deployment or release
claim follows from these repairs.
