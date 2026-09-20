# First speaker-aware hearing result

## Delivered

Pinned Sortformer v2.1 plus Multitalker Parakeet 0.6B ran on actual human recorded
speech and **passed all seven predeclared engineering cases**. Predicted speaker
activity, not reference masks, conditioned the recognizer. Each two-speaker case
produced two separate anonymous text streams. No enrolled person's name was
provided or inferred. MeetEval 0.4.3 independently agrees with every error count.

| Case | Speaker streams | Word errors / reference words | cpWER |
| --- | --- | --- | --- |
| Speaker A alone | 1 | 0 / 15 | 0% |
| Speaker B alone | 1 | 0 / 12 | 0% |
| Alternating, A re-enters | 2 | 0 / 42 | 0% |
| Equal-volume overlap | 2 | 3 / 54 | 5.56% |
| B quieter during overlap (gain 0.25) | 2 | 0 / 54 | 0% |
| A quieter during overlap (gain 0.25) | 2 | 1 / 54 | 1.85% |
| Overlapping entry without clean introduction | 2 | 1 / 27 | 3.70% |

The aggregate is 5 edits / 258 reference words (1.94%). This is seven constructed
cases using **two source recordings**, not seven independent conversations. It
is not a population accuracy estimate. The frozen gate was case cpWER <= 25%,
each speaker WER <= 35%, and the correct count of nonempty speaker streams.
The worst per-speaker WER was 12.5%. That gate is a first engineering hurdle,
not a definition of human-level speech.

A second complete execution with the final hash-checking runner passed all seven
cases again and produced **byte-identical SegLST output**. It took 98.70 seconds
including startup/verification. The panel was independently rebuilt and its
manifest and audio digests were identical. The model-free source scope passed
55 tests (44 existing package/catalog checks plus 11 new scorer/runner checks),
with no skips. No unchanged native suite or hardware gate is represented as new
qualification.

One global anonymous-speaker permutation is chosen per complete recording.
Tracks cannot be re-matched at each segment to hide identity swaps. Dropped
speakers and additional text are counted. Unit falsifiers cover collapsed
mixtures, missing speakers, track swaps, unexpected sessions and clock overshoot.
No DER, word-timing accuracy or tcpWER is claimed: clip placements are not
speech-activity or word-alignment truth.

## Example: simultaneous words survive separately

In the cold-overlap case, the model output includes these overlapping extents:

- `speaker_0`, 1.68–5.84 s: “I get tired of seeing men and horses going up and down,
  up and down”
- `speaker_1`, 3.44–7.20 s: “Was in a corner that he lay among weeds and nettles”

The second stream missed the initial “It.” The two voices were not merged into
one transcript or both named the operator. These are model timestamps, not independently
validated word boundaries.

## Execution and custody

- Isolated source branch: `feat/speaker-aware-hearing-20260920`, based on public
  voice `079cff3ecd68fa94838b3d529e563bc68a1748f0`.
- Upstream NeMo source: `f613eed86ed4696db0891aac4e9104337a39142c`; clean at execution.
- Sortformer checkpoint SHA256:
  `8abd32832159c6ac1148c926b7276f35ba34582c444e559dce1f1253fea42ef8`.
- Multitalker Parakeet checkpoint SHA256:
  `afaefe89829e201a0ee22c67a715eb5a467bd06c0704cc1579fbfb370fb5be73`.
- Frozen panel SHA256:
  `0679ce6af085114ef1d567a295f5a15c9606a60d81783dad1ef767461c8d18d9`.
- Result SHA256: `e2ead4334cab3dd59fadaed29eb3c8bb2c6c8853c4d1f8a388fa0fc91abeee71`.
- Raw SegLST output SHA256:
  `d1a374d52eefc1c45f2d11f88162ed84d63195d9892dee6d4df71fd2c943f201`.
- Independent scorer check SHA256:
  `ef987018390b9870ea71130481b4eb97e1cf7007d31e5a8c4bb426066921be66`.

Evidence is under `test-results/reference-cpu-r1/` in the isolated local checkout,
and `build-host:/path/to/work/work/voice-speaker-aware-20260920/reference-cpu-r1/`.
The result includes model pins, package versions, exact inference command and
input/output hashes. Both the inference child and runner exited; their process
IDs were independently absent after completion.

This run used CPU FP32, two threads, a low-priority process, no reference
diarization, no enrollment data, no supplied transcript and no expected speaker
count. It processed 101.10 seconds of audio in 110.39 seconds **including startup
and verification**. It was unpaced file streaming, not a measured live response
latency. build-host's GPU had only about 2 GB free, so its live workloads were left
alone. No GPU/NPU, mobile, installed/browser or endurance qualification follows
from this run.

## What this does and does not close

**Closed at reference level:** the selected SOTA direction can recover separate
speaker streams for our first overlap cases. This is measured execution, not a
model-card inference. The pipeline, fixed inputs and scorer can be rerun.

**Still open:** natural multi-party conversation; short/quiet/distant speakers;
speaker-count limits; sample-clock accuracy; false matches and unknown-person
rejection; matching clean speaker evidence to enrollment; bounded live revisions;
native numerical parity; accelerator advantage; interruption/recovery with the
new hearing engine; five-platform and installed-product qualification.

The published beta.4 bytes are unchanged and still have the pooled-utterance
overlap defect. No UID threshold was relaxed, profile altered, live identity
restarted, release uploaded or catalog changed.

## Next implementation boundary

1. Preserve this output as the native port's behavioral reference. Export and
   check the actual streaming state and speaker-kernel tensors before changing
   the native encoder. The checked checkpoint uses feed-forward foreground and
   background kernels before encoder layer 0; the background kernel consumes the already updated
   foreground residual. Omitting that order or treating this as ordinary Parakeet
   is not a faithful port. Exported graphs must expose the speaker targets as
   actual inputs: exporting with absent targets can freeze unconditional defaults
   and silently remove the feature. Test target changes on the same audio.
2. Preserve independent per-speaker encoder/decoder caches and the shared audio
   clock, including final padded chunks. Anonymous track IDs are session-local,
   not enrolled person IDs. Upstream C++ support for ordinary ASR does not prove
   this speaker-conditioned model is supported.
3. Match enrollment only from suitable speaker-specific evidence. Test clean
   regions and overlap separately; do not run the existing pooled embedder on
   the entire mixed utterance and call the new transcript identified.
4. Hand explicit segments and revision cases to the host agent and Test Identity A before
   freezing a public SDK representation. Host owns joins and prompt rendering;
   voice owns attribution, native parity and fast interruption integration.
5. Qualify CPU/GPU/NPU placement by measured advantage per platform. Keep the
   working TTS/VAD/turn and carrier foundation intact while this hearing path is
   integrated. This result is not a reason to switch production to Python.
