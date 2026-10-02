# Bounded diarization evidence refinement (experimental)

This is an internal candidate, not a claim of reliable overlap identification.
The resident native model factory enables `NemotronConfig::refine_evidence`
for Nemotron hearing, because the replay runs on a copy of the diarizer stream
(see "Bounds and lifecycle"). Legacy hearing is unchanged. Standalone
components keep the explicit opt-in for paired qualification; no new operator
setting or SDK method is needed. A source build is not installed qualification.

The replay first re-fed the live diarizer stream, and was turned off in the
resident path on 2026-10-01: with the Linux Full models (CUDA diarizer) on the
three identity-mixture recordings, every replay was refused by the exact-mask
test, yet the next utterance, compared with the same continuation after no
replay, differed every time: activity by up to 0.11, 0.22 and 0.46; dominant
track in 8, 1 and 175 of 451 frames; one transcript track in the third.

Measured again on 2026-10-01 on macOS (Metal diarizer, Full and Small ASR) with
`aii_refinement_lifecycle_probe`, before and after the replay moved to a stream
copy. The replay outcomes are the same before and after, and so is every
refined result (`aii_multitalker_microphone_probe --refine-evidence` output is
identical apart from timing):

| Recording | Replay | Next utterance, live-stream replay | Next utterance, copy |
| --- | --- | --- | --- |
| Identity mixture 1 | refused | activity up to 0.107867; dominant track 8/451 frames | exactly equal |
| Identity mixture 2 | refused | 0.217605; 1/451 (Small: one transcript track) | exactly equal |
| Identity mixture 3 | refused | 0.455416; 175/451; one transcript track | exactly equal |
| 24.4 s solo turn | accepted | 0.188518; 0/2440 | exactly equal |

On that solo turn the resident recognizer adapter withholds identity evidence
as `speaker_track_coverage_unverified` without refinement and selects 10 s of
evidence with it (`aii_multitalker_recognizer_evidence_probe`). This is one
private recording, not speaker-accuracy or installed qualification.

## Contract

The recognizer may refine a completed utterance whose clean identity evidence
is withheld by uncertain competition. Confident overlap, already usable
evidence, and absent/short evidence do not trigger extra inference.

One native diarization-only pass reuses the **same captured PCM**, with the
native speaker memory retained from the initial pass. There is no ASR replay,
new model, threshold change, enrollment input, label input, or text correction.
The same audio and track slots must remain bound to the original transcript.

The engine records every binary per-track mask actually consumed by ASR, in
its original chunk geometry. It downsamples refined activity through the same
`nemotron_targets` implementation and requires exact mask equality for every
track and frame, including partial/padded tails. Equality preserves foreground,
background union, dormant-track activation, and the ASR cache transition
sequence. If any mask, frame extent or track slot differs, original evidence
is retained. Similar-looking text is never a substitute for this test.

When masks match, identity evidence is reselected from that same PCM through
the unchanged `SpeakerEvidence` and `EvidenceAudio` policies. Overlap or
uncertain competition in the refined evidence still withholds whole-final
identity. This does **not** establish that the diarizer is acoustically right;
that requires independent solo, unknown-speaker and overlap acceptance data.

## Bounds and lifecycle

- At most 32 seconds of mono 16 kHz float PCM is retained for refinement.
  Exceeding the limit discards the entire refinement capture, never just the
  beginning of a longer transcript. Existing recognition continues unchanged.
- Exactly one attempt follows a successfully finished utterance. Native calls
  receive at most one second of PCM each; cancellation is checked between
  calls and after finishing. Native inference is not asynchronously destroyed.
- PCM and refined native activity are released after completion or failure.
- A cancelled/faulted replay cannot continue the speaker cache. A fresh
  session resets it.
- The replay runs on a deep copy of the live diarizer stream
  (`nemo_speech_diar_stream_clone`, see `NEMOTRON_NATIVE_DESKTOPS.md`), taken
  where a replay on the live stream would start: after the finished utterance,
  before its next-utterance boundary. Its starting memory, and so its
  exact-mask outcome, is unchanged. The live stream is never fed: the next
  utterance continues from exactly the speaker memory the live pass left,
  whether the replay is accepted, refused, cancelled or faulted. The copy
  shares only the model's weights and backends, runs sequentially on the
  inference thread, and is closed on every path.
- No new SDK method, host authority, speaker-label rule, or package declaration
  is introduced by this candidate.

## Proof boundaries

`aii_refinement_test` covers changed foreground/background/slot masks, exact
thresholds, malformed probability/extent, partial tails, whole-final competition
and the retention limit. `aii_refinement_activity_probe` checks recorded native
activity through the compiled guard; it does not run ASR.

`aii_multitalker_microphone_probe --refine-evidence` exercises real native ASR
and refinement. Its `activity` field remains the original ASR activity; its
`attribution_evidence` reflects the guarded final decision. The status and
elapsed time explicitly distinguish accepted, refused and unavailable replay.
`aii_multitalker_recognizer_evidence_probe --refine-evidence` exercises the
actual resident recognizer adapter. `aii_refinement_lifecycle_probe` covers
continuation, in-flight cancellation, duplicate refusal and exact fresh-session
recovery using a real model and one or more recordings, whichever outcome
their replays have. For every recording it compares the continuation after its
replay with the same continuation after none, both on fresh sessions, and exits
non-zero unless activity values and every track's tokens are exactly equal
(`next_utterance_unchanged_by_replay`), for accepted and refused replays alike.
It also reports the replay outcome, the largest activity difference, frames
whose dominant track changed and transcript tracks that changed.

`aii_nemotron_replay_test` (CTest) runs the replay against a model double of
the pinned diarizer C API whose speaker memory, like AOSC, survives the
utterance boundary and moves with every pushed sample. It requires, for an
accepted and a refused replay, predictions equal to those a replay on the live
stream would produce and an exactly unchanged live stream and next utterance;
after cancel and fault, the same live stream and next utterance; and the copy
closed on every path, including a frame-extent mismatch and a refused replay
before finish.

Recorded component evidence is not an installed browser test, broad speaker
accuracy, or three-platform release qualification. Those remain required
before production promotion. Private signed candidates may exercise containment
without claiming those broader gates.

`scripts.prove_native_uid_refinement` drives four distinct recordings from the
same speaker through the packaged resident lane. The first pair must establish
one profile; every final from the next pair must carry that UUID, including
after process retirement and restart. It also checks interruption, recovery,
post-session labeling, exact final/observation joins, unchanged profile storage,
and a three-second attribution-delivery bound. A withheld known-speaker result
fails this gate; passing it does not measure false accepts of other speakers.

Optional `--nonmatching-recordings` supplies bounded recordings that contain
none of the acquired speaker. Each still needs exact final/track observations
within three seconds, but must never receive that speaker's UUID. This is a
false-attribution regression, not proof of correct separation or stable UUIDs
for the other speakers. An unresolved observation is not a successful speaker
separation. Input completion is not identity completion: the test waits for the
asynchronous observations using their final/track joins and original deadlines.
