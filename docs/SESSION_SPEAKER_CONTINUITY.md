# Session speaker state, not per-utterance amnesia

The microphone's acoustic frontend and the four ASR decoder/encoder states
restart at an utterance boundary. The bounded diarization cache now survives
that boundary within the same capture session. A fresh session clears it.
Cancellation or a failed capture cannot be continued as a healthy utterance.

`Recognizer::open` marks the next capture as fresh. `begin` explicitly selects
fresh versus continued capture; `reset` retires only utterance state. Lower-level
probe callers retain the default fresh-capture behavior unless they explicitly
request continuation. Activity/sample clocks, recognition text and selected
PCM remain utterance-local; a past paragraph is not emitted again.

The cache retains at most 376 frames of 512 floats. Cache persistence preserves
the model's evidence for assigning anonymous speaker slots; it is **not proof
that a slot is the same human**, and it does not grant a durable UUID or any
command authority.

## Executable proof

Build `aii_multitalker_continuity_probe` with the exact model's ONNX Runtime
headers/library. Supply the bound graph root, 128-bin mel coefficients and an
approved 16 kHz mono float32 recording. No reference speaker or transcript is
passed into inference.

The probe requires:

1. Retained cache at the end of an actual recorded utterance.
2. The same retained frame count immediately after beginning the next
   utterance, with fresh utterance clocks and bounded subsequent growth.
3. An empty cache at a new session, with exactly the original isolated token
   and activity outputs when replaying the same recording.
4. Refusal to continue a cancelled capture, followed by an isolated recovery
   whose token/activity outputs again match the baseline.

Initial real-model Mac proof used 24.5195 seconds of approved recorded audio:
307 retained frames after the first utterance, 370 after continuation, and
successful session isolation/cancellation recovery. Restoring the old
unconditional cache reset fails with `utterance discarded speaker cache`.
All 44 native tests also passed in the real-composition build.

## What this does not settle

Clean-region collection still ends with each utterance. The existing registry
corroborates independently embedded samples from distinct utterances; it does
not pool short raw snippets across them. Blindly concatenating track 0 from
successive utterances could join different humans and corrupt both enrollment
and recognition. Cross-utterance accumulation therefore still needs measured
speaker continuity, explicit sample provenance, bounded retention and tests
that reject slot switches, quiet overlap and unknown-speaker contamination.

The present recognition output has per-track conservative utterance extents,
not word-aligned speaker UUIDs. Neither that missing capability nor broad UID
accuracy is qualified by the cache/lifecycle proof. Calibration must use held-out
microphone conditions and unknown speakers, keeping rejection, false identity
acceptance and UUID fragmentation separate. Speculative listening remains after
those correctness gates.

## Missing identity audio is not a failed identity match

The recognizer now carries an explicit reason when a transcript track has no
eligible identity audio. The reason reflects the activity selector, not an
independent ground-truth judgement about the recording:

| Reason | Observed condition |
| --- | --- |
| `speaker_track_coverage_unverified` | A clean island exists, but concurrent confident activity elsewhere in this final has no isolated waveform. The island cannot name the whole final. |
| `speaker_evidence_expired` | Selected audio has left the bounded input ring. |
| `speaker_overlap_without_isolated_evidence` | Concurrent confident activity was observed, and clean evidence is insufficient. |
| `speaker_activity_uncertain` | Uncertain activity was observed, and clean evidence is insufficient. |
| `speaker_evidence_too_short` | Confident activity exists but not enough guarded clean audio. |
| `speaker_activity_unavailable` | No confident or uncertain activity for this track. |
| `speaker_separation_failed` | The opt-in separating composition found coverage or overlap competition above, but its separator or a source recognition failed to run or exceeded its latency budget. The live transcript is kept unresolved. |

The last reason replaces only the coverage and overlap reasons after
selection; it carries no model message. Without detected overlap, usable selected audio
takes precedence over the remaining diagnostics. Otherwise the reason
precedence is the table order;
this is not a claim that one condition
was the recording's sole problem. Counters use the model's sample cadence,
retain no additional audio, and clear with the utterance. The existing evidence
duration, activity thresholds and boundary guards are unchanged.

The bounded collector replaces an earlier selection when a new complete
region supplies more active speech than the old selection plus any safely
appendable part. This prevents confidently silent gaps in a sparse prefix
from occupying the entire ten-second allowance and excluding a later clean
sentence. Original sample regions remain attached; neither activity thresholds
nor the conservative active-time bound on a clipped region is relaxed.
`multitalker_speaker_evidence_contracts` includes the sparse-prefix/clean-tail
regression and retention of an already stronger selection.

For an explicit missing-evidence reason, the session preserves the final text
and immediately emits its bound unresolved speaker observation. It does not
enqueue empty PCM behind actual UID inference, query the identity gallery,
mint a UUID, or invent a match score. Unknown reasons and contradictory
nonempty evidence fault before emitting finals. Recognizers which do not
supply this optional internal diagnostic retain their existing behavior.

The session contracts test each reason with a deliberately stalled matcher,
verify that no matcher call occurs, and require both transcript tracks and
their observations to retire normally. The selector contracts cover both
model cadences, clean-region preservation, expiry and reset. These tests prove
bounded failure handling, **not reliable identification of overlapping speech**.
Wholly overlapping speech still needs qualified speaker-specific acoustic
evidence; a remembered track number is not a substitute for it.

## A clean excerpt cannot identify overlapping words

The present adapter supplies an unresolved observation when its diarizer
reports concurrent confident activity anywhere in a text track, even if it
also found a clean prefix or suffix. Its transcript remains intact, and the
next single-speaker utterance is evaluated normally. The native session does
not enqueue that excerpt for matching, enrollment evidence or UUID creation.
This closes a known false-attribution path; it does **not** recover overlap
identity or detect a speaker change which the diarizer itself missed. Quiet
overlap, short utterances, and full word-to-speaker attribution remain acoustic
qualification gates. No confidence threshold, registry or SDK rule changes.
