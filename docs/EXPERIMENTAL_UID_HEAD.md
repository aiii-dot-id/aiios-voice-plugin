# Experimental projection-head UID checkpoint

This test branch binds ECAPA model
`46aef1e6f483a2033eb88344b3d0d09b21d177c15cf45e45b0451d207de1fc99`.
Only the final projection weight and bias changed. The preprocessing contract,
two-second evidence floor, 0.4 acceptance threshold, 0.14 competing-profile
margin and overlap-withholding rules are unchanged.

Training used a speaker-disjoint public LibriSpeech dev-other partition,
with clean-preservation and separation-artifact pairs. Ridge strength 100 was
chosen on validation before evaluation. A previously exercised public acceptance
panel improved from 59/72 to 62/72 known overlap records and 177/180 to 178/180
solo controls, with no wrong accepts among 267 unknown records. This still
fails complete known-speaker coverage and is not a broad accuracy estimate.

This is an operator-authorized test candidate, not production qualification.
The exact embedding binding changes: baseline profiles must be retained for
rollback but not interpreted as embeddings from this checkpoint. The model
requires new test enrollment or separately validated re-embedding from audio.
No UUID/name migration is inferred from a matching dimension count.

The graph remains open-weight derived from the original SpeechBrain checkpoint;
its upstream notices remain required. Model weights and private test recordings
are not source files. Experimental deployment does not enable the unqualified
separator backend or claim that every overlap is detected.

On 2026-10-01 the operator promoted this checkpoint as the released UID model
for 0.1.0-beta.7. The ruling does not change the failed acceptance result
above or make it production qualification; see
[OVERLAP_IDENTITY_GATE.md](OVERLAP_IDENTITY_GATE.md). Profiles from earlier
bindings, including 0.1.0-beta.5's ResNet152-LM and the original ECAPA
projection, are reported incompatible and never matched. Confirmed recovery
archives them; those speakers re-enroll.

## Training data record

Verified at the source on 2026-10-01: https://www.openslr.org/12/ describes the
LibriSpeech ASR corpus (OpenSLR identifier SLR12), "License: CC BY 4.0"
(https://creativecommons.org/licenses/by/4.0/), prepared by Vassil Panayotov
with the assistance of Daniel Povey and derived from read audiobooks from the
LibriVox project. The projection was fitted on its dev-other partition. The
separated half of each separation-artifact pair is output of the plugin's
MossFormer2 separator, whose own terms ship with its notices. The shipped
attribution is `runtime/native_uid_ecapa/notices/NOTICE`.
