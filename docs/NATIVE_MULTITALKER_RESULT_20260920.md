# Native speaker-conditioned recognition checkpoint

Historical component checkpoint. The subsequent
[native hearing integration](NATIVE_HEARING_INTEGRATION_20260921.md) connects
raw PCM, diarization and the resident SDK path; its remaining release limits
are stated separately. The measurements below retain their original scope.

This checkpoint adds native C++ recognition components to beta.5 development
source. **It is not yet connected to the plugin worker. No package, public
release, catalog entry, live identity or enrollment was changed.** The installed
speaker-ID repair remains mandatory for the next release.

## Implemented and measured

- Six ONNX boundaries exported: shared-capture ASR and diarization pre-encoders,
  speaker-conditioned ASR encoder, RNNT prediction network, joiner, and
  diarization classifier. Eighteen synthetic numerical cases pass, both
  pre-encoders reject multiple captures, and blank-token priming matches the
  reference's empty-token priming.
- Native C++ speaker-conditioned encoder, prediction network and joiner execute
  with independent encoder and recurrent decoder state per anonymous speaker
  track. Four tracks are supported by the interface; the recorded panel actually
  exercises two, not four.
- Decoder-only and combined encoder/decoder executions reproduce **all 405
  reference tokens across 110 updates and seven conversations**. Inputs are
  real recorded-speech features and model-predicted speaker masks, not supplied
  reference words or ground-truth speaker masks.
- Removing speaker conditioning fails the token comparison at record 6.
  Sharing one encoder cache across speakers fails at record 19. A crash or
  malformed input does not count as a successful mutation test.
- Model-free checks cover track independence, re-entry, blank-state handling,
  stale epochs, replayed frames, invalid geometry, nonfinite input, fault
  retirement and cancellation while inference is deliberately stalled. After
  cancellation, the next model stage is not invoked.

The recorded cases are isolated controls, alternating speakers, equal-level
overlap, two unequal-level overlap cases, and overlap from a cold start. Token
parity preserves the reference's measured errors; it does **not** mean perfect
transcription. The reference panel scored five errors over 258 words.

## Exact boundary

The native combined proof begins **after shared audio preprocessing** and uses
the reference's inferred foreground/background masks. It replaces the acoustic
encoder and the autoregressive decoder/joiner with C++/ONNX Runtime, owning its
own continuing caches. Reference caches never enter native inference. Expected
tokens are read by the probe only after the native call returns.

The capture feature clock, diarization state/cache management, speaker gating,
token-to-segment assembly, clean-evidence enrollment matching and resident SDK
integration still need native composition. The trace's frame counter is local
to each track's consumed encoder frames; it is not absolute audio timing. No
fixture-derived names enter recognition.

This CPU development adapter is not an accelerator selection for release.
GPU/NPU use still requires measured advantage and actual placement proof.
Neither an ORT export nor a C++ build qualifies mobile or desktop installation.

## Numerical failure retained, contract corrected

The first three export attempts retained a two-capture batched ASR pre-encoder
failure: a near-zero output differed by approximately 0.00026, outside the
existing absolute tolerance. Disabling graph optimizations did not close it.
It has not been described as passing or fixed by widening tolerance.

The reference pre-encodes one microphone capture once and expands its result
for speakers **after** preprocessing. The interface now enforces that ownership:
pre-encoder batch size is exactly one, and a two-capture input must fail.
Speaker batches remain supported and tested on the conditioned encoder and
prediction network. The original numerical threshold is unchanged
(`rtol=0.001`, `atol=0.0002`); integer clocks are exact and NaNs always fail.
The previous multi-capture export is not qualified for reuse.

## Provenance

Upstream NeMo and model pins are unchanged from
[the reference result](SPEAKER_AWARE_REFERENCE_RESULT_20260920.md).

- Six-graph result SHA-256:
  `6c44bc3b6c41cd116bf53550cae7ae30b35881ecf9af9fedc320a8e28d4befb9`
- Recorded-trace result SHA-256:
  `33a6e4cf2a7cba4f6d29480cfa520bb0c323052e9e3689318d5d97bdb3afe35f`
- Decoder trace SHA-256:
  `880020a8fefc7eade862f34dbfca31b9ed15ce50e6dbb173dc682eb717ec0aea`
- Conditioned trace SHA-256:
  `6fad5ff0b11ce0690eebfc872b7de1d4a4e2e11b8a9dee41e054e1ac605f3530`
- Final native proof result SHA-256:
  `33b223059b7a0b5482c3bd255987a3bcdcc9bc2b22315933ef63b0c4349f5211`
- Native probe SHA-256:
  `b243e2610ef871a7f975284e84041cf51a1367ecc4fcdb3452d40db561a4583e`

Tracing preserved the reference SegLST output byte for byte. The native result
binds the probe, ORT library, source files, graph inventory and both traces.
Execution used ONNX Runtime 1.24.3 on Linux CPU; model export used PyTorch 2.14.0.
The six FP32 graph artifacts total 3,023,391,865 bytes in 301 files, including
configuration and vocabulary. This is a development inventory, not an optimized
or signed distribution payload.

## Reproduction

Use the isolated pinned reference environment, never a live identity.

```sh
PYTHONPATH=/absolute/pinned-NeMo-checkout CUDA_VISIBLE_DEVICES= \
  python -m scripts.export_speaker_aware_graphs \
  --nemo /absolute/pinned-NeMo-checkout \
  --models /absolute/models/manifest.json --out test-results/new-graphs

PYTHONPATH=/absolute/pinned-NeMo-checkout CUDA_VISIBLE_DEVICES= \
  python -m scripts.trace_multitalker_decoder \
  --nemo /absolute/pinned-NeMo-checkout \
  --reference /absolute/passing-reference/result.json --out test-results/new-trace

cmake -S runtime/native_multitalker -B .build/multitalker-native \
  -DCMAKE_BUILD_TYPE=Release \
  -DORT_INCLUDE=/absolute/onnxruntime/include \
  -DORT_LIBRARY=/absolute/onnxruntime/lib/libonnxruntime.so
cmake --build .build/multitalker-native

python -m scripts.prove_native_multitalker \
  --graphs test-results/new-graphs --trace test-results/new-trace \
  --probe .build/multitalker-native/aii_multitalker_trace_probe \
  --ort-library /absolute/onnxruntime/lib/libonnxruntime.so \
  --out test-results/new-native-proof
```

The library must be resolvable by the platform loader. Every output directory
must be new; failures are retained. A failed graph set cannot enter the native
qualification runner. Generated models, traces and private evidence are not
committed or uploaded with source.

## Remaining release work

Connect native diarization and microphone-clock preprocessing, assemble stable
speaker segments, and attach enrolled-person observations only from suitable
speaker-specific evidence. Then connect resident SDK/host joins and qualify
the final installed images on Windows, Ubuntu and macOS. Re-run interruption,
recovery, settings and meeting endurance on those bytes before signing and
publishing. Existing single-speaker plugin tests do not close these gates.
