# Native Nemotron streaming component

The existing checkpoint-aligned recognizer, implemented in C++17 with a private
C ABI. Python does **not** own its frontend, caches, token loop, cancellation or
result state. The independent build/evaluation harness uses Python; the native
executable does not. No model has been retrained, replaced, or converted here.

The caller supplies the exact, verified immutable model directory and 128×257
float32 mel coefficients from the existing model catalog. There is no search,
download, alternate vocabulary, implicit language selection, or provider fallback.
This first implementation explicitly selects CPU. GPU bindings and device-level
qualification remain required before it replaces a deployed recognizer.

## Ownership contract

- `aii_asr_create`: loads the three existing ONNX graphs, validates their geometry
  and vocabulary. It requires `ORT_DISABLE_TELEMETRY=1` before initialization.
  The existing catalog owner remains responsible for hashes/640 weight-span
  readback. This C ABI does not introduce a second manifest authority.
- `aii_asr_stream_create`: independent recurrent/cache state. A stream keeps its
  models alive even if the model handle is retired.
- One owner calls `accept`, `step`, `finish`, `result`, `stats`, and stream destroy.
  `accept` consumes finite mono 16 kHz float32, at most 32,000 samples per call,
  30 minutes per stream. It never runs inference. Invalid input does not advance
  the source sample count. `step` performs at most one acoustic window.
- Completed windows retire only raw PCM that no later overlapping feature
  window needs, including its pre-emphasis predecessor. Recurrent caches,
  tokens and absolute clocks never reset. `aii_asr_buffer_stats` reports first,
  retained and total samples separately without enlarging the existing stats ABI.
  The retained-buffer allocation remains bounded at 61 seconds (3,904,000 bytes);
  callers must step instead of indefinitely admitting undecoded audio.
- `cancel` and atomic `busy`/`phase` observation run from another thread without
  taking the model-owner lock. Cancellation fences result retrieval immediately;
  already executing ONNX work retires at its next checked boundary. Admission
  latency and actual worker retirement are different measurements.
- Return codes: `step` returns 1 for a window, 0 for not-ready/exhausted, 2 for
  cancelled, -1 for failure. Other integer functions use 0 for success, 2 for
  cancellation, -1 for refusal/failure. A model-run fault poisons that stream.
- `finish` adds 10,560 **model-context-only** zero samples. `source_samples` never
  includes these; capture/VAD/UID/duration must not consume them. Drain `step`
  until 0 and require `input_finished && exhausted && !cancelled && !failed`.
  Empty capture is handled by the session owner, not by inventing an STT final.
- The caller must retire the model owner before destroying a stream; a busy
  destroy is refused. It must fence publication by session/generation as well:
  a text snapshot fetched before cancellation is not permission to publish later.
- Result buffers refuse insufficient capacity rather than silently truncate.
  Language is the same current English-qualified path; no multilingual
  qualification or speaker identity claim is implied by vocabulary contents.

`aii_asr_probe` is a recorded-PCM proof driver, **not** the SDK resident engine.
It takes no expected text or reference features and opens no microphone/speaker.
The public audio/control/receipt wire is unchanged. The C library is intended to
be composed into that owner, including app-bundled mobile callers.

## Current evidence

The sealed loader in `initializers.cpp` now maps, hashes and binds the original
640 checkpoint spans directly; sessions open verified graph bytes, not external
tensor paths. The external initializer mappings are construction-local: ORT's
`AddExternalInitializers` copies their data into the graph. After session load
and integrity checks, the referring options and original mappings retire. The
native fixture runs an unoptimized Add after both have retired to verify this
ownership boundary. See
`deliverables/native-asr-portable-20260912-r3/README.md`: identical final/token/
partial trajectories on all three desktops, native corruption/retirement tests,
three compiled mutation kills, Mac SDK regression, Pixel component execution
and iOS linkage. These do not replace the broader qualification limits below.

[Native component proof](../../deliverables/native-asr-cpp-20260911-r3/README.md):
nine frontend cases, ten complete recognition cases with every partial trajectory
matching the prior owner, actual in-flight cancellation and retained-word recovery.
Five further complete process retirements, two compiled mutations, and frontend
AddressSanitizer/UndefinedBehaviorSanitizer checks pass.

The continuous-input proof in `deliverables/native-asr-continuous-20260912-r2`
preserves all ten prior final/token/trajectory cases and matches the full-history
reference over 66.24 seconds at both 512- and 73-sample packet sizes: 552 tokens,
every intermediate transcript equal, at most 15,937 valid samples retained in
those long runs. The test reference alone receives a larger PCM allocation;
its feature math and recognition code are unchanged. This is storage/continuity
qualification, not a speedup: the two native unpaced runs took about 10.8 seconds
versus 9.74 seconds for that reference.

This is a usable native **STT component**, not the complete Python-free voice
resident, GPU qualification, the frozen 192-case panel, an installed signed
plugin, or a human-level claim. Preserve CP1 until the full composition earns it.
