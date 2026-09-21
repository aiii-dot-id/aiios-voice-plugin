# Native hearing integration, September 21

This is a resident-engine integration result, **not a signed or installed beta
release**. Persistent anonymous speaker UUIDs and speaker-specific person
matching are not implemented by this change. Local acoustic tracks must not be
presented as either capability.

## Production path now connected

The opt-in `AII_MULTITALKER_ASR` native composition processes real 16 kHz mono
PCM through the pinned frontend, shared capture pre-encoders, streaming
diarization/cache, inferred foreground/background masks, separate acoustic
encoder caches, and independent recurrent decoders. Its vocabulary becomes
separate transcript finals with exact session-relative capture spans and local
track references. The C API and resident worker preserve those references.

No reference transcript, reference mask or reference hidden state enters that
pipeline. The raw native implementation reproduces all 405 reference tokens
across the seven frozen engineering cases on Linux and macOS. This preserves
the reference's errors; it is not perfect transcription. The panel contains
two original recordings arranged into seven cases, not a population sample.

The resident session does not submit mixed microphone PCM as per-track UID
evidence. Each final receives an explicit uncertain attribution until the
speaker-specific evidence owner is implemented. This prevents a pooled match
from naming another speaker's words but does not provide usable named UID.

## Integration failure found and repaired

The first real SDK run passed six cases but failed the quieter-second-speaker
case: ten edits in that speaker's 24 words (41.7%), beyond the unchanged 35%
per-speaker engineering ceiling. Raw native recognition of that case had zero
errors. The session had removed 4,608 initial samples through its VAD pre-roll
gate, shifting the context-sensitive model's feature origin.

Capture-context recognizers now consume actual input continuously, including
idle context. VAD still controls speech activity, interruption and turn
commitment; it no longer trims this recognizer's input. Existing recognizers
keep their prior gated behavior. The implementation remains bounded and does
not retain an entire conversation's PCM. No synthetic padding replaces the
discarded audio and no threshold was loosened to pass the fixture.

The repaired five-model, hash-bound native carrier passed all seven cases
through the real SDK. The quieter-second-speaker case returned to zero errors;
the seven-case total was four edits over 258 words. Every case exercised spoken
interruption of real synthesis, separate finals, matching observation references,
recovery synthesis, receipt-gated drain, and process retirement. Sink receipts
were simulated; this was not an installed browser or physical-audio test.

Regression tests additionally prove silence does not invent a turn, initial
capture is not discarded or duplicated, context survives a pause/next-turn
transition without dropped or repeated input, malformed separated output faults
before emitting any final, and pooled input never reaches the UID matcher.

## Distribution mechanics

The six-graph download layout reduces 301 files to 12. External tensor data is
repacked into files no larger than 512 MiB. Every tensor byte and every protobuf
field other than its external location/offset is compared exactly. Raw native
recognition on the repacked layout again matched all 405 reference tokens.
This meets the SDK's existing model-count bounds without changing them.

The checkpoint rebuilder accepts explicit graph/frontend bindings, preserves
all non-hearing models, includes the native implementation's license/notice,
rebinds the carrier, and clears inherited execution/release claims. Generated
models, recordings, enrollment data and local proof paths are not source assets.
Required runtime notices and executable modes are protected by archive tests.

## Remaining release gates

- Persistent anonymous profiles, evidence-backed returning-speaker linkage,
  later naming, revisions and UUID filters; never substitute a local track ID.
- Speaker-specific UID evidence and installed consumer joins, including overlap,
  ambiguous continuity and stale amendments.
- Track-specific partials and segmentation finer than the conservative
  utterance capture span; the current adapter emits separate finals only.
- Final changed-model runtime qualification on Windows and Ubuntu 24.04, and
  installed/browser journeys on all three desktops. Model-free tests pass on
  all three desktops; that is not changed-model execution on all three.
- Appropriate accelerator selection backed by measurements. New hearing uses
  CPU; the recorded Mac composition still uses Metal TTS.
- Public model notices, signed final assets, anonymous fresh-download/install
  verification, then the single catalog entry derived from the signed package.

No public release or catalog entry is advanced by these tests.
