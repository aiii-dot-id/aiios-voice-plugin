# Speaker-aware hearing execution

Authorized by the operator on 2026-09-20 after the source/SOTA review. Base: public
voice `079cff3ecd68fa94838b3d529e563bc68a1748f0`. Work stays isolated from
Test Identity A, Test Identity B, their enrollment documents, and the published beta.4 assets.

## Delivery order and exit evidence

1. **Recover both speakers' words.** Run pinned NVIDIA Multitalker Parakeet
   0.6B with streaming Sortformer v2.1 on hash-bound public recorded speech:
   isolated controls, alternating speakers, simultaneous speakers at equal and
   unequal levels, and re-entry. Score every stream, including extra and empty
   outputs. Compare against a deliberately collapsed one-track falsifier.
   A successful model execution is not an accuracy pass. References never enter
   the recognizer. Start with an isolated reference runtime, not a live plugin.
2. **Make the result usable.** Own sample-clocked anonymous tracks and explicit
   pending/unresolved/matched identity observations. Match enrollment only from
   suitable single-speaker evidence; never name a whole mixture. Carry revisions
   against the original segment. Host/SDK changes belong to the host agent.
3. **Integrate and qualify native hearing.** Preserve current SDK control,
   cancellation and playback receipts. Prove native numerical/output parity,
   bounded state, interruption and recovery; then installed desktop and mobile
   paths. An upstream C++ model list is not multitalker native support.
4. **Extend quality without disrupting hearing delivery.** Keep Silero,
   Smart Turn v3.2 and compact Pocket TTS. Compare Qwen3-TTS for a richer profile,
   and enable existing Nemotron 3.5 language prompts only with per-language
   tests. Evaluate upstream native runtime reuse and platform accelerators with
   actual model placement, memory, latency and energy evidence.

## Contract constraints

- Anonymous track identity and enrolled person identity are different things.
- Every text segment has explicit attribution state. Missing metadata is not a
  match, and no match does not prove a different person is present.
- Concurrent speech yields separately attributed text or explicit unresolved
  coverage. Suppressing all overlap is containment, not feature completion.
- Voice matching is evidence, never authenticated command authority.
- Fast interruption does not wait for diarization, transcription, or UID.
- No words or speaker profiles are supplied as recognition hints by the scorer.
- Fixture clip boundaries are not word-level speech-activity ground truth.
  Report cpWER and per-speaker coverage first; do not invent DER/tcpWER.
- No rate, power or latency claim from an unpaced CPU run becomes a GPU, mobile,
  live-browser, eight-hour, or human-level claim.
- No public push, package replacement, enrollment mutation, or live deployment
  is part of this first gate.

## Initial pins

- Sortformer v2.1: `nvidia/diar_streaming_sortformer_4spk-v2.1`, revision
  `fafaab5faa1617a0ca52d38dd3dc4bd636800d3d`.
- Multitalker Parakeet: `nvidia/multitalker-parakeet-streaming-0.6b-v1`, revision
  `8749fc71fd6e2d88ef230159bbf2aea69b524ee1`.
- Upstream inference source: NVIDIA/NeMo
  `f613eed86ed4696db0891aac4e9104337a39142c`.
- The earlier MLX Sortformer feed-clock defect remains an explicit parity
  regression when that backend is considered; it does not justify patching
  the NeMo clock without a failing example.

## Coordination

Test Identity A owns consumer observations and proposed acceptance fixtures. The voice
agent owns engine implementation and qualification. The AII OS agent owns
durable/live annotation joins, filtering and prompt rendering. Messages are
immutable files in the existing exchanges; posting is not acknowledgement.

## First gate result

The pinned reference executed on 2026-09-20 and passed all seven frozen cases.
See [measured results and remaining gates](SPEAKER_AWARE_REFERENCE_RESULT_20260920.md).
Native integration and reliable enrolled-person attribution are not yet closed.

The operator subsequently made those repairs mandatory for the next plugin
release. They are no longer an optional challenger alongside a privacy-only
package. See [the release requirements](BETA5_PREPARATION.md).

## Cached encoder export

`scripts/export_speaker_conditioned_encoder.py` exports the pinned Multitalker
encoder with explicit foreground/background speaker inputs and external cache
tensors. It retains the upstream kernel hooks, including foreground-before-
background residual order. Exporting an ordinary unconditioned encoder is not
an alternative. The checker refuses a missing input, shape/dtype mismatch,
nonfinite output, changed integer cache length, or identical output for opposing
speaker targets on identical input.

Run in the pinned reference environment with ONNX Runtime 1.24.3 installed:

```sh
PYTHONPATH=/absolute/pinned-NeMo-checkout CUDA_VISIBLE_DEVICES= \
  python scripts/export_speaker_conditioned_encoder.py \
  --nemo /absolute/pinned-NeMo-checkout \
  --model /absolute/pinned-multitalker-checkpoint.nemo \
  --out test-results/new-cached-encoder-export
```

This is a development export and numerical parity gate, not a shipping model
declaration or native speech session. Its fixed-shape input is synthetic mel
features. It does not test diarization, words, enrollment, actual final audio
padding, live latency or installed behavior. Each continuation consumes its own
runtime's prior state; reference and ORT caches are not cross-fed. Model outputs
and external tensor files stay outside source control. Preserve failed exports.

## Reproduce the reference, not a production installation

Use an isolated Python 3.12 environment with the pinned NeMo checkout installed
with its ASR extras, plus torchaudio, matplotlib and MeetEval. The tested package
versions are in the execution result; this is development tooling only.

```sh
python scripts/acquire_speaker_aware_models.py --output /absolute/new/model-directory
python scripts/prepare_speaker_aware_panel.py \
  --source-panel /absolute/fresh-speakers-panel-20260909-r1 \
  --output test-results/speaker-aware-panel-r1
python scripts/run_speaker_aware_reference.py \
  --panel test-results/speaker-aware-panel-r1/panel.json \
  --models /absolute/new/model-directory/manifest.json \
  --nemo /absolute/pinned-NeMo-checkout --device cpu --threads 2 \
  --output test-results/new-reference-run
python scripts/check_speaker_aware_meeteval.py \
  --panel test-results/speaker-aware-panel-r1/panel.json \
  --hypotheses test-results/new-reference-run/hypotheses.json \
  --output test-results/new-reference-run/meeteval-check.json
```

The source recordings are external evidence, not bundled in Git. The panel
builder refuses a changed source manifest or WAV. All output directories must
be new. The recognizer manifest is regenerated with target-machine paths and
file durations only; reference text/activity/identity never enters inference.
