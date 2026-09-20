# Speaker-conditioned cached encoder export

The pinned Multitalker encoder exported to ONNX and passed eight numerical
checks against its upstream implementation on September 20, 2026. This is a
development integration result, **not a repaired installed voice plugin**.

## What executed

The exported graph retains seven runtime inputs, including foreground and
background speaker targets and three cache tensors. The upstream speaker-kernel
hooks remain attached; background conditioning consumes the updated foreground
residual. Both implementations ran CPU FP32 with two threads.

The checked cases are foreground, background, overlap, silence, independent
foreground continuation, independent background continuation, shortened final
length, and a fresh call after another track. Continuations use each runtime's
own prior state, not reference state substituted into ORT or vice versa.

- Eight cases passed output count, exact geometry/dtype, finite-value and
  numerical checks. Integer lengths must be exact; floats use rtol 0.001 and
  atol 0.0002. Maximum observed absolute difference was 0.000354766845703125;
  the combined absolute/relative tolerance, not absolute tolerance alone,
  determines the verdict.
- Opposing speaker targets on identical synthetic mel input changed encoded
  values by up to 0.20034870505332947. Targets were not discarded or frozen.
- A fresh call after another track agrees with the initial fresh output.
- The final source-bound run took 44.025 seconds including restoration,
  export, checks and hashing. This is not streaming latency or throughput.
- The export contains 300 files and 2,495,319,208 bytes. This unoptimized FP32
  export is not a final distribution layout or resource declaration.

## Exact bindings

- NeMo: `f613eed86ed4696db0891aac4e9104337a39142c`, verified clean and imported
  from that checkout.
- Checkpoint SHA256:
  `afaefe89829e201a0ee22c67a715eb5a467bd06c0704cc1579fbfb370fb5be73`.
- Exporter SHA256:
  `1620dc6c3748e446ec5c4a94b988263d1b804dba34fcf8d7d195fd4f4e5da0fe`.
- Result SHA256:
  `52915d5eaa4b712f2a7d1ffb0254d00fd2e8309d4fd4d4abb18961adfb403697`.
- Graph SHA256:
  `624d47ca094b66a725b957adc2fa63397843f2e612afd91f5af98d84221536a1`.
  The graph digest alone does not bind its external weights; the result carries
  all 300 artifact sizes and digests.
- Torch 2.14.0+cu130, ONNX Runtime 1.24.3. GPU execution was explicitly disabled
  for this parity run; the package name is not evidence of CUDA use.

Five model-free regression tests reject missing speaker/cache inputs, broadcast
shape agreement, dtype changes, wrong integer cache lengths, nonfinite values,
missing outputs, an unconditioned result and wrong-cache numerical results.

## Limits and next integration boundary

The synthetic input has fixed shape `[1,128,121]`; both target inputs are
`[1,16]`. No waveform was transcribed by this export test. A shortened length
does not establish correct real-audio tail padding. The eight numerical cases
are not the seven recorded-speech accuracy cases from the earlier reference.

The native frontend, Sortformer streaming state, RNNT decoder and session-owned
track lifecycle must still be connected and compared on the recorded panel.
Then speaker-specific evidence must bind tracks to enrollment and reach the
identity through the host's segment/revision joins and filters. Interruption,
recovery, endurance and installed desktop acceptance must run on those final
bytes. The model's anonymous tracks are not enrolled identities.

Publication remains blocked by [the next-release requirements](BETA5_PREPARATION.md).
No release, catalog, enrollment or live identity was changed by this export.
