# Overlap identity: safety repair and remaining qualification

Status: **not qualified for reliable overlap identity**. Released desktop sets
select the resident separation composition described below through their
sealed hearing profile. That is a release decision recorded with each release;
it is not a passed overlap-identity gate, and nothing here makes it one.

## Repaired production boundary

The recognizer produces speaker-conditioned text, but its identity evidence is
selected from the original microphone waveform. A clean prefix or suffix can
identify that excerpt; it cannot establish the identity of overlapping words
elsewhere in the same final. When the diarizer detects concurrent confident
activity on a track, the adapter now withholds the identity evidence for that
final. The same rule covers a competing track above the existing inactivity
ceiling (0.1) but below confident activity (0.9) while the target is active or
uncertain. Two uncertain tracks are not evidence of one verified speaker.
A quieter possible competitor must not inherit the clean island's identity
just because it never reaches the confident-overlap threshold. Own-track
uncertainty still trims evidence but is not counted as a competing voice.
The rule preserves the transcript and emits an explicit unresolved reason.
It never sends this excerpt to matching, enrollment or UUID admission. The
next clean utterance is evaluated normally.

### Native diagnostic and acceptance boundary

The microphone probe's `selected_evidence` and `evidence_spans` describe raw
clean islands, not permission to identify the whole transcript. Its separate
`attribution_evidence`, bound to `attribution_policy=whole-final-coverage-v1`,
now calls the same coverage selector as the resident recognizer. It includes
the permitted sample count, original-clock regions, unresolved reason and
observed competing-activity counts. No waveform, embedding or person label is
included in this additional report.

The composition grader refuses older reports without that decision and reports
permitted versus raw evidence separately. A transcription pass explicitly does
not qualify speaker identity. The registry-audio proof consumes only permitted
regions and fails its overall acceptance when required known-speaker coverage
is incomplete; correct withholding alone cannot make it green. Existing
historical reports are retained, not retroactively rewritten.

The native contracts cover both supported activity cadences, confident and
uncertain competition before and after the clean excerpt, the 0.1 boundary,
unchanged text, no matcher job, and recovery.
Restoring the old selection rule makes the new regression fail. A recorded
public-speech probe through the actual recognizer retained evidence for two
solo voices and alternating speech, and withheld it for all four overlap
cases. This proves the detected-overlap boundary, not detection of every
overlap or speaker change.

`aii_multitalker_recognizer_evidence_probe` uses the actual bound recognizer.
It takes the graph root, mel table, exactly 1,024 length-prefixed tokens
projected from the model's own `tokens.json`, native diarizer model, device
index and float32 recordings. It reports only text lengths, extents and
evidence disposition; it does not print transcripts or waveforms.

## Corrected waveform-to-UID comparison

An earlier offline comparison was invalid: it graded raw floating-point
separated waveforms but silently saturated those same outputs when converting
to PCM16 for speaker encoding. Scale-invariant separator outputs were about
75 times the input scale. That experiment cannot support a conclusion that
the separator itself destroyed speaker identity. Its failed result must be
preserved with this correction, not silently replaced.

`scripts.separator_audio` restores output RMS using the upstream decoder's
normalization, bounds the peak and refuses invalid PCM instead of clipping
model errors. Tests include scale, integer overflow, non-finite values,
truncated tails, silence and both PCM16 endpoints.

`python -m scripts.prove_separator_uid --help` describes the source-bound
component gate. It pins upstream source and checkpoint, verifies recording
hashes, uses the original three-reference galleries, holds the threshold and
margin fixed, and makes decisions before consulting truth for permutation
grading. No live enrollment or registry is opened. This is a previously
exercised public corpus with correlated mixtures, not a blind independent
corpus or a broad accuracy estimate.

The corrected 16 kHz MossFormer2 comparison on equal and 12 dB unequal
mixtures produced:

| Split | Clean known controls | Known separated outputs | Wrong accepts across separated outputs |
| --- | --- | --- | --- |
| Calibration | 6/6 | 31/36 | 0/72 |
| Speaker-disjoint evaluation | 6/6 | 34/36 | 0/72 |

The evaluation misses were withheld by the unchanged competing-profile margin.
They are failures of the required known-speaker coverage, not successes. A
separate corrected 8 kHz SepFormer comparison was worse (26/36 calibration,
29/36 evaluation, no wrong accepts). Adding that second model is not justified
by this evidence. These results apply to three-reference galleries; they do
not establish equivalent performance with a single enrollment sample.

## Native export repair

The first numerical divergence was the initial GroupNorm: the export lowered
its multi-million-element reduction to float32 InstanceNormalization. The
encoder immediately before it was identical. The exporter now copies the
model and accumulates centered normalization statistics in float64, preserving
the original affine parameters and casting back to the original dtype. Its
reference remains the untouched upstream model, not the converted model.
No comparison tolerance or identity threshold was relaxed.

Both independent exports produced the same graph bytes. All five lengths
(32,000, 32,776, 72,000, 80,000 and 80,003 samples) passed the original
relative-error limit of 0.0002. The exporter keeps hashed input/reference
fixtures so another executable can test the exact same expectations.

Two further export/provider defects are repaired without changing weights:

- The FSMN adapter removes only its inserted singleton channel. Upstream's
  axis-free squeeze erased singleton batch/time dimensions and relied on the
  residual addition to restore them, leaving an ambiguous ONNX rank.
- Constant last-element Gather indices use the actual dimension length.
  A five-operation reproducer showed the Core ML conversion select the wrong
  element for a symbolic extent, breaking later matrix dimensions. The repair
  has CPU semantic tests and an explicit Core ML regression. Empty dimensions
  still refuse; other indices are not reinterpreted.

The `aii_separator_execution_probe` is a development target, not a shipped
worker or enabled runtime backend. The repaired graph passed all five lengths
through native C++ ONNX Runtime on each desktop. It also passed an interrupted
inference and an unchanged-reference recovery in the same session for every
length. Profiling must show actual requested-provider execution in baseline,
interrupted and recovery calls; provider availability alone cannot pass.

`python -m scripts.prove_native_separator --help` describes the executable
gate. It verifies graph, input, reference and binary hashes, preserves failed
process exits, bounds execution, checks output order and numerical error, and
refuses missing profiles, CPU-only accelerator fallback and absent recovery.
The thread count is explicit and is checked against each completed native
call's report. Inputs are seeded diagnostic signals, not recordings.

The following measurements precede the explicit contraction export. They do
not qualify a later graph or composition; each changed graph needs fresh proof.

| Native path | Warm inference, 4.5-second input | Qualification boundary |
| --- | --- | --- |
| macOS CPU, 12 threads, ORT 1.24.2 | 3.89 seconds | Numerical/cancellation pass; sampled resident peak 1.52 GiB |
| macOS Core ML, ORT 1.24.2 | 8.20 seconds | Numerical/cancellation pass; resource qualification fails |
| Ubuntu CUDA, ORT 1.24.2 | 0.478 seconds | Numerical/cancellation pass |
| Windows VM CUDA, ORT 1.22.1 | 1.49 seconds | Numerical/cancellation pass; isolated component runtime |

These timings are component observations on shared hardware, not latency
distributions or end-to-end conversation measurements. Windows VM timings
must not be generalized to dedicated Windows hardware. Cancellation is
requested about 100 milliseconds after starting inference in the current
development probe. This lets a warmed VM call reach GPU work before termination;
the profile still must prove that it did. The established 50-millisecond
admission and 250-millisecond retirement bounds are unchanged. Earlier
25-millisecond calls cancelled before any GPU kernel remain failed evidence,
not accelerator passes. Later-inference cancellation, long recordings and
installed lifecycle remain to be qualified.
Core ML profiling proves provider execution, not the physical GPU/ANE choice
inside that provider. This work does not claim NPU execution.

The official Windows GPU ORT 1.24.2 package loaded but could not execute its
first CUDA kernel on the tested Pascal GPU. The unchanged GPU/driver passed
with the official GPU ORT 1.22.1 package and compatible CUDA/cuDNN libraries.
This is a package architecture-coverage issue, not proof that Windows cannot
run local speech. An API-24-built production worker must not silently receive
an API-22 runtime: rebuilding and qualifying every model in that worker is a
separate requirement. Nothing was changed in the installed runtime.

The original monolithic Mac Core ML path was **not** selected on the strength
of its numerical pass. A
bounded follow-up exceeded a 16 GiB experimental resident-memory ceiling when
input length changed. Another dynamic-shape variant exceeded 100 GiB and was
terminated; its partial outputs are a failure, not acceptance. A fixed-length
graph was faster, but does not establish variable-length or acoustic parity.
The CPU thread comparison is faster, much smaller and currently preferable
on the tested Mac. No production memory reservation is inferred from the
experimental ceiling; the CPU peak was sampled every half second, not an
exhaustive allocation bound. The later staged native adapter supersedes this
monolithic failure for its finite qualified input range; see the update below.

Run `python -m unittest tests.test_separator_export -v` in the export environment
with PyTorch, ONNX and ONNX Runtime installed. Numerical regressions
cover long reductions, dynamic lengths, affine/no-affine parameters, batches,
groups, tensor ranks, silence, the singleton FSMN channel and preservation of
the reference model. Run `python -m unittest tests.qualify_separator_coreml -v`
separately on a Mac with Core ML; missing provider fails rather than skips. This
gate requires its dependencies rather than silently skipping. It complements,
but does not replace, full-model export and native execution.

An additional calibration-only test projected the separated outputs back
onto the original mixture, using either equal or source-energy residual
weights. Both reduced correct known matches from 31/36 to 30/36, with no wrong
accepts in any arm. Neither change is used by the runtime or exporter.

## Native audio-first binding

`aii_multitalker_source_binding_probe` now composes the actual native separator
with the actual recognizer. Each output waveform is recognized independently,
with fresh per-capture diarizer state. Every selected identity sample must
equal the corresponding sample in that same waveform, at its reported original
clock position. No pooled transcript is reassigned to a separator output. An
output number is acoustic provenance, never a person or UUID. The existing
unknown/overlap disposition survives unchanged. Both source results validate
before either is returned; cancellation or invalid evidence discards the batch.

This is a finite, offline composition seam, **not yet a streaming worker
backend**. Its bounded inputs do not establish resource bounds for long live
sessions. Neither two outputs nor one diarizer slot proves that every word
belongs to one person. Three-plus-speaker mixtures, hidden leakage, sequential
speaker changes and word attribution still need independent acceptance.

The model-free contracts cover independent recognition, source-order reversal,
exact PCM/region binding, contradictory evidence, missing evidence, silence,
invalid second-source output, cancellation between and during source calls,
and recovery. Mutations that reuse source zero, remove the PCM comparison, or
ignore cancellation each fail the regression at execution, not compilation.

The recorded Mac screen used three solo controls and six mixtures: two known
voices, and known-plus-unknown, at equal and 12 dB unequal gains. Each input is
4.5 seconds. It produced 15 separate text/evidence records: **9 of 11 known
records identified correctly, both remaining known records unresolved, all
four unknown records unresolved, zero observed wrong accepts**. This is only
three speakers from the previously exercised corpus, not a broad accuracy
estimate. The threshold remains 0.4 and the margin 0.14. The two misses are the
same quiet-voice cases found with the untouched upstream model. Encoding the
whole separated waveform rather than its selected region still fails the
margin, localizing these failures beyond the evidence-cropping boundary.

The texts are separately produced, but the screen has no independent
word-aligned truth and therefore does not claim WER or complete word retention.
No enrollment labels or truth assignment enter inference; truth is used only
afterward to grade the separator permutation and embedding decisions. Durable
UUID registry behavior is not exercised by this component screen.

Actual native cancellation was requested one second into separation and
250 ms into source recognition. Retirement after the requests measured 0.73 ms
and 16.04 ms respectively; the same owners then processed all nine inputs.
Those are two tested points, not an all-phase interruption guarantee. On the
tested Mac, medians were 3.92 seconds for separation and 3.03 seconds for
recognition, 6.93 seconds combined. **This finite offline path is not acceptable
interactive latency.** What the released sets select is the resident
composition described below, under its own latency budget; that selection is a
release decision recorded with each release, not a passed overlap-identity gate.

The probe takes graph root, mel table, 1,024 framed tokens, Nemotron model and
device, separator ONNX, separator thread count and CUDA device (-1 for CPU),
a new private output directory, and float32 recordings. Its caller must verify
all model bytes and external-weight inventories before construction. It writes
transcripts and selected waveforms explicitly for private grading; it must not
be used as a public-log or installed-identity tool. Its interruption timing is
for the measured Mac path; different providers need their own bounded timing.

## Staged native adapter and exact-path update

The staged Core ML adapter now passes the unchanged waveform parity limit,
GPU-configured cancellation and recovery on finite inputs from 32,000 through
80,003 samples. Compact compiled stages occupy 227,194,776 bytes. A recorded
Mac composition measured a median 1.875 seconds for separation plus recognition
on 4.5-second inputs. Those are component observations, not installed latency,
physical accelerator placement, or continuous-session qualification. The
export, staging, compiler and probes are documented in
`runtime/native_multitalker/README.md`. No Python is needed by that adapter.

The broader acceptance check must use the recognizer-selected PCM, not the
entire separated waveform. On 72 paired captures, the unchanged encoder
identified 59 of 72 known source records; all 72 unknown records remained
unresolved. Six known records had no qualified evidence and remain in the
denominator. Whole-waveform numbers above are not a substitute for this gate.

Two frozen adaptations were fitted on a separate public development corpus
repurposed as training data: 273 selected separated/clean pairs from 24 speakers,
clean controls from 25 speakers, and eight additional validation speakers.
None occur in the existing acceptance panel. A query-only residual map reached
61/72; an update to the existing encoder's final projection reached 62/72.
The projection candidate re-embeds the original enrollment recordings with the
same candidate model used for queries; it does not combine incompatible model
spaces. Solo clean/noise/microphone-condition matches improved from 177/180 to
178/180. No wrong acceptance was observed among the 72 mixture and 195 solo
unknown controls. Correlated variants and a reused public panel do not establish
population accuracy, independent blind performance, or authentication.

Both adaptations **fail acceptance** and are not enabled in the plugin.
For the projection head, six known records lack enough guarded evidence and
four remain acoustic matching failures. The two-second evidence floor,
160 ms boundary guards, 0.4 score threshold and 0.14 competing-profile margin
were not relaxed. The head changes model bytes and would require a new model
binding and explicit profile transition even if it passed. Full-utterance
attribution now also refuses detected uncertain competition; the source repair
is independent of these unpromoted training artifacts.

On 2026-10-01 the operator promoted the projection head as the released UID
model for 0.1.0-beta.7, although it does not meet this document's acceptance
bar. This is a release ruling, not a passed gate. The measurements above stand:
62/72 known records (six without enough guarded evidence, four acoustic
matching failures), 178/180 solo controls, and no wrong acceptance among the
72 mixture and 195 solo unknown controls. The evidence floor, guards, threshold
and margin are unchanged. The released model is ECAPA
`46aef1e6f483a2033eb88344b3d0d09b21d177c15cf45e45b0451d207de1fc99`, binding
`67f55477cc9da7029cad6032cbb49e52ae7b6194dc4629b00d23facf3b741502`. Profiles
enrolled under 0.1.0-beta.5's ResNet152-LM binding
(`2575d15495d0e1cf17a2a12b0163dec0639db6218b3fe810e70369002d3898da`) or the
original ECAPA projection
(`ffe5c3d33b41fef374cc147df0061adc25e56ef35b44ed53977e906dac4e29f1`) keep no
audio to re-embed. They are never matched or converted: `speaker.list` reports
them `incompatible`, confirmed recovery archives the original files, and those
speakers must re-enroll.

## Still blocked

### Failure analysis and bounded replay correction

The fixed-panel nine remaining abstentions after the replay-eligibility
correction are variants of three utterances, not nine independent speakers.
Five reuse one cropped utterance: only 1.83–1.96 seconds of guarded evidence
remain against the two-second floor. The original clean crop also fails;
its uncut recording and four other uncut utterances from that person pass.
Four other failures are quieter separated voices, about 11–16 dB below their
competitors. Correct identities rank first but fail score/margin admission;
clean audio from the same selected regions passes. One evidence-limited case
also remains a matching failure when scored as a whole separated waveform.
Whole-waveform counterfactual scores are not admissible whole-final proof.

Bounded replay now also considers fragmented own-track confidence when enough
active speech exists before guarding. It never admits that fragmented PCM
directly: exact ASR-mask equality, duration, competition and boundary checks
remain unchanged. The paired native check moves known coverage from 62/72 to
63/72, preserves text and reports no wrong acceptance among 72 unknown source
records. This is a reused development regression, not broad accuracy or an
installed separator qualification. The earlier failed reports remain valid.

A separate prespecified 1.75-second crop comparison on eight validation speakers
did not preserve the two-second controls' coverage in every crop position.
No new wrong acceptance occurred, but the duration-floor change was not selected.
The production floor remains two seconds. Coverage, wrong attribution and
time-to-identity must be assessed separately; perfect recognition of these
correlated crops is not, by itself, a production-readiness definition.

### Resident composition checkpoint (not release acceptance)

The resident model factory now has an opt-in separation composition selected
only by its immutable hearing execution profile. No SDK method, operator
authority or live identity setting was added. The default profile remains
unchanged. `separator: {"backend":"coreml"}` requires the eight verified
compiled stages under `stt/separator-coreml`; an ONNX profile declares
`{"backend":"onnx","threads":N,"cuda":D}` and requires
`stt/separator.onnx`. Missing data or an unavailable declared provider refuses
startup rather than falling back.

Detected competition triggers independent recognition of both separated
waveforms. Each final and its identity evidence come from the same waveform;
the original mixed text is never reassigned. A separate recognition context
preserves the live session's diarizer history. That currently duplicates ASR
weights, so memory is a qualification cost, not an assumed free abstraction.
No new person is inferred from a source index.

The composition retains at most the selected separator's finite input bound:
80,003 samples at 16 kHz for both the staged Core ML and the ONNX adapter.
The live capture is continuous, so Session names each turn's VAD onset preroll
inside it. The separator window starts there, not at the previous turn's end:
silence before speech does not spend the bound. Separated text and identity
samples keep their original capture-clock positions. A turn shorter than the
two-second floor reuses earlier captured audio to reach it, as before.
A turn longer than the bound counted from its preroll, a shorter-than-two-second
capture and more than two detected text tracks retain the existing unresolved
transcript. They are not truncated or claimed as separated. A replacement also
requires text from both source outputs; disappearance of one output retains
the original unresolved records.
Cancellation reaches live recognition, source recognition and separation
without waiting behind inference. A failed/cancelled partial result cannot
escape. A separator or source recognition that fails to run (memory, provider
error, non-finite or misshapen output) no longer ends the session: the
completed live transcript is kept and its competing tracks report
`speaker_separation_failed`, never the model's message. The source recognizer
is retired and the next turn separates normally. Cancellation, a progress
refusal and an invalid source result still fail the turn.

Separation is best effort under a latency budget. One separator call may use
5 times the separated audio's duration, clamped to 4 through 25 seconds by
default (`SeparationBudget`: 25 s for the 80,003-sample window; the limits
table's `separation_min_ms` and `separation_max_ms`), which the table holds
below the model-call watchdog's time, 30 s by default. CPU separation of one 5 s window was measured at
7.0-7.3 s on a Ryzen 9 7950X3D desktop, 9-11 s on an Ubuntu laptop (Core Ultra
7 155H) at full clocks, about 12 s at the 2.35 GHz it averaged under a
sustained lifecycle run, and 30-45 s while its firmware capped package power at
12-17 W. That last case once ran past the watchdog and ended the session. A
2.5x budget abandoned the laptop's uncapped separations too; 5x keeps them and
abandons only the power-capped ones.
At expiry only the separator is cancelled, never the session; a
late result is abandoned, the turn keeps its live records as above, and the
separator is re-armed for the next turn. A session cancel during separation
is still immediate cancellation. The watchdog, window and models are unchanged.
`status.model_execution.asr` reports the budget and counts why competing turns
were or were not separated (`replaced`, `incomplete_sources`, `budget_expired`,
`separator_failed`, `source_failed`, `window_exceeded`, `below_minimum`,
`too_many_tracks`) with the latest outcome; these are fixed tokens only.
Long-turn chunking and missed/quiet competition remain unqualified.

The first private packaged Mac SDK run produced independent source finals for
three fixed mixtures, preserved interruption/recovery and subsequent solo
transcription, and retired the process. It failed full identity acceptance:
one of two public test speakers did not acquire a UUID from three clean
recordings. Overlap finalization took 5.8–6.9 seconds with two encoder threads;
sampled worker RSS reached about 8.7 GB. These failures remain evidence, not
reasons to relax identity policy. This is not signed, installed or browser
qualification and is not a production candidate.

Repeating the same packaged SDK cases with the supported eight-thread encoder
setting reduced overlap finalization to 2.9–3.6 seconds. Identity outcomes were
unchanged; sampled worker RSS remained about 8.7 GB. The unchanged nine-recording
SDK regression also passed with this composition enabled, including durable
identity, unknown-speaker controls and interruption/recovery. Its longer
overlap controls exceed the Core ML bound and retain unresolved attribution;
that pass is not evidence of long-turn separation. Complete native contracts
passed on macOS (51), Ubuntu 24.04 (52) and Windows (53). The latter two are
source contracts, not new resident-separator model or installed qualification.

The acquisition failure was examined separately. All three clean recordings
had 3.1–4.1 seconds of permitted evidence. The first pair scored about 0.364,
below 0.4; the third scored about 0.485 and 0.504 against the two pending
recordings, below the required margin between them. The original encoder has
the same pattern, so the projection-head update did not introduce it. Pending
recordings are not proven distinct people, but simply removing their competing
margin would also admit a mixture between two different people. No such policy
change was made.

An additional run retained the failed three-recording result and measured
acquisition over eight distinct clean utterances per speaker. The previously
pending speaker acquired a UUID on the fourth; the other on the second.
Both then resolved in the equal-level and one unequal-level mixture. The
other quieter output remained below threshold, and the first speaker's
original recording failed to match its subsequently acquired profile. This
failed the then-used complete-identification gate, not a pass obtained by
extending enrollment. That gate conflated abstention with identity corruption;
the distinction and subsequent measurements are recorded below.
Observed finalization in this later shared-machine run ranged from 2.4 to
6.3 seconds; the earlier faster timings are not a latency guarantee.

1. **Production separator integration and resource bounds.** Numerical parity
   and early cancellation/recovery now pass on all three desktops. Mac CPU
   and both tested CUDA paths are faster than real time on these diagnostic
   inputs. This is not sufficient latency headroom or proof of long-input,
   late-cancellation, memory and installed lifecycle bounds. The staged Mac
   adapter has only finite-input qualification; Windows' compatible runtime still requires full-worker
   integration. The released desktop sets select the resident composition
   above through their sealed hearing profile, by a release decision recorded
   with each release; that is not a pass of this item.
2. **Short-speech identity.** One-second diagnostics lost many correct clean,
   noisy and microphone-shift matches. Lowering the production duration floor
   is not justified. Concatenating speech merely because it occupied the same
   diarizer slot could combine different speakers and is not a repair.
3. **Known-speaker coverage and complete text-to-speaker evidence.** Native
   source binding now preserves each waveform's own text and identity samples,
   but the broader selected-audio evaluation still fails as detailed above.
   Current final extents are conservative
   utterance extents, not word alignments. Quiet overlap missed by the diarizer
   and sequential speaker changes inside one model slot remain unqualified.
4. **Installed and live acceptance.** Passing these source and component checks
   cannot substitute for the signed desktop package, installed lifecycle,
   browser playback, microphone changes and unknown-speaker acceptance set.

No identity thresholds, labels, registry documents, SDK contracts or installed
packages were changed to make these comparisons pass.

### Identity correctness versus coverage

The acceptance contract is now explicit in [IDENTITY_ACCEPTANCE.md](IDENTITY_ACCEPTANCE.md).
A quiet abstention is not a wrong UUID or a duplicate person. Coverage must be
reported separately; all-abstention is not evidence of a useful recognizer.
The new scorer checks exact final/observation ownership and selects track
permutations from independent text before comparing UUIDs. Swapped UUIDs cannot
pass merely because the set of identifiers is correct.

A completed 28-utterance Mac recorded SDK lifecycle passed with unchanged
runtime bytes and identity thresholds: two acquired speakers, repeated quiet
inputs, three complete-source mixtures, a third unknown speaker, interruption,
recovery and restart. No duplicate UUID or wrong-person assignment was observed.
Quiet coverage was 7/8 and overlap coverage 5/6. Both established speakers
recovered their original UUID after process restart; an older solo recording
still abstained. The three mixtures each measured 4.17% speaker-aligned word
error; overlap finalization took 3.38–3.64 seconds and peak worker RSS was 8.74 GB.
These measurements are a finite, reused-corpus result, not population accuracy,
physical-room quality, latency guarantees or installed qualification.

An earlier run incorrectly compared cropped mixture audio with full-utterance
reference text. That failed report is retained, but its word-error values cannot
establish full-utterance quality. The replacement selected complete recordings
by duration and corpus order before inference, without trimming or deriving
reference text from the recognizer. Both measurements and the criterion change
remain visible; the positive result does not erase broader coverage limitations.
