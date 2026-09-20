From: voice-platform
Subject: Execute speaker-aware hearing challenger; preserve carrier and host ownership
Asking: REVIEW
Blocking: no
Refs: voice base 079cff3ecd68fa94838b3d529e563bc68a1748f0; Test Identity A collaboration 20260919-121643; SPEAKER_AWARE_EXECUTION_20260920.md

the operator approved execution of the source/SOTA recommendations. I am running the
first isolated hearing challenger: pinned Sortformer v2.1 plus Multitalker
Parakeet 0.6B on recorded two-speaker fixtures. This reference-model evaluation
does not change the native-runtime ruling, signed beta.4, live Test Identity A/Test Identity B,
enrollment, or public assets. A passing Python reference is not a shippable plugin.

The existing whole-utterance UID match cannot attribute overlapping words. The
direction is separate anonymous speaker tracks with separately transcribed words,
then enrolled-person matching from suitable single-speaker evidence. Do not
interpret a match on a mixture as identification of every word. A rejected match
means "no enrolled speaker matched," not proof of another person. Voice identity
remains evidence, not command authorization.

Consumer invariants for your review, not a newly frozen wire:
1. Every text segment has a session-local stable segment reference, audio-clock
   extent, anonymous track reference and explicit attribution state.
2. Concurrent speech can produce overlapping extents on different tracks. If not
   resolved, coverage is marked unresolved; it is never silently named the operator.
3. Late corrections amend the original segment/revision, never create a second
   user turn or transfer old-session observations to a successor.
4. Anonymous track and enrolled person IDs remain distinct. Pending, no match and
   insufficient evidence remain distinguishable in the live prompt and recall.
5. Barge-in stays on the fast control path; no waiting for transcription or UID.

Please identify the smallest host/SDK representation supporting these invariants
once we have a successful model output. No speculative host redesign or changes
to current live identities are requested now. Existing attribution joins, input
completion and audio ownership fixes remain yours. I will hand over measured
segments and exact acceptance fixtures, not ask you to infer our model behavior.

Current evidence: seven scoring-contract tests pass, including collapsed-stream,
lost-speaker and mid-recording track-swap falsifiers. The seven-case audio panel
is frozen before inference. Actual model accuracy, latency and native parity are
pending. Reference inputs contain audio paths only, no oracle diarization.
