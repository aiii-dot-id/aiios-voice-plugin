# Joint speaker identification acceptance

The enrolled and anonymous galleries are two stores, not two independent
elections. An accepted winner must clear its own runner-up and the best
candidate in the other gallery by the bound policy's minimum margin.
Ambiguity between two weaker candidates in the losing gallery cannot veto
that winner. Close global competitors still remain unresolved. No threshold,
enrollment minimum, profile, link or model changes implement this correction.

## Regression and recorded evidence

`native_guided_enrollment_publication` tests both winning-gallery directions,
close cross-gallery competitors, absent speakers and unchanged persistence.
The weak-losing-gallery case fails with the predecessor decision ordering.

The development runners additionally support read-only copies of real evidence:

- `aii_multitalker_microphone_probe` reports the production guarded selector's
  exact selected PCM regions; `--continue` preserves diarizer state between
  supplied utterances. It reports token IDs, not invented word timestamps.
- `aii_uid_snapshot_probe policy snapshot queries` applies the native snapshot
  codec and matcher to a bounded JSON array of encoded query vectors.
- `aii_capture_enrollment_test --observation-panel policy enrollment registry
  queries` runs the production registry owner against the test's isolated
  in-memory host. Each query has `embedding`, `samples`, and `pcm_sha256`; each
  is a distinct utterance. This is not an installed broker or overlap test.

The private recording replay on 2026-09-24 recovered one previously rejected
enrolled match: winner score 0.756 versus the other gallery's best 0.580,
exceeding the unchanged 0.105 required margin. The other seven usable query
embeddings retained their prior outcomes. Twenty-five public-speaker negative
controls produced zero enrolled matches before and after, both with an empty
anonymous gallery and with the copied existing gallery. This small control is
not a population false-accept estimate. Private recordings, vectors, names and
gallery documents are deliberately absent from this source tree.

## Earlier continuity failure and subsequent repairs

Five diagnostic microphone recordings produced usable evidence independently.
In a continuous-state replay, the last two lost usable evidence. In the
isolated empty-registry arm the first recording remained pending, the second
created one profile, and the third matched that profile; the missing evidence
on the final two must not disappear from the denominator. The existing-gallery
arm also retained unresolved close competitors. Neither arm qualifies reliable
speaker identity, word attribution or overlap handling.

A diagnostic change refreshing compressed-cache scores restored some evidence
but split another transcript across two tracks. It is not included in this
repair. A lifecycle/cache-retention pass cannot replace the acoustic gate.

The next acceptance must count every recognized track, including missing
evidence; require stable attribution or explicit abstention; bind outputs to
actual source samples; and retain separate checks for unknown speakers and
quiet overlap. Changing the live gallery or thresholds to make a replay pass
is not a substitute for that repair.

The native Nemotron composition subsequently supplies clean evidence for all
five positive recordings; see [native desktops](NEMOTRON_NATIVE_DESKTOPS.md).
The matching-readiness correction in [profile admission](PROFILE_ADMISSION.md)
then changes the copied-gallery decisions from 1/5 to 5/5 enrolled matches,
without changing those recordings, vectors, profiles, thresholds or margins.
All 161 recordings from 25 absent public speakers remain rejected on macOS and
Ubuntu. These component results
do not replace the still-required installed conversation gate.
