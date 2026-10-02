# Native speaker-conditioned recognition component

This component is selected by the desktop worker built with
`AII_MULTITALKER_ASR`; that worker's package and model bindings require their
own exact qualification. This component alone is not a plugin, enrolled-person
matcher, or mobile runtime.

The encoder owns separate channel, temporal and valid-length caches for up to
four legacy or eight Nemotron anonymous acoustic tracks. The RNNT decoder owns a separate last token and
LSTM state for each track. A quiet track keeps its own state when another speaks.
Epochs advance at session reset; stale epochs and replayed decoder frames are
refused. Model faults poison an epoch instead of allowing partially mutated
state to be retried. Cancellation sets an atomic fence and requests ORT run
termination without waiting for inference. The owner must retire inference
before resetting or destroying the objects.

Blank predictions do not advance the LSTM state. Their prediction is cached
until a nonblank token is emitted. Each push returns only new tokens, with
encoder-frame indices; the decoder does not retain an ever-growing transcript.
The encoder retains the pinned model's 70-frame channel cache. Both targets
(foreground and background) remain inputs to every encoder call.

The standalone ONNX adapter proof uses CPU. A desktop package may select the
adapter only with its sealed assets, accelerator declaration and resident-session
evidence; the standalone test does not qualify CUDA, DirectML, CoreML, mobile
GPU or NPU placement. Existing TTS, VAD, interruption and playback-receipt
behavior has not been replaced.

## Checks

Model-free state contracts:

```sh
cmake -S runtime/native_multitalker -B .build/multitalker -DCMAKE_BUILD_TYPE=Release
cmake --build .build/multitalker
ctest --test-dir .build/multitalker --output-on-failure --no-tests=error
```

Supplying explicit `ORT_INCLUDE` and `ORT_LIBRARY` also builds the ONNX adapter
and recorded-trace probe.
Private recordings/features and generated models never belong in this directory.

The finite separated-source composition validates both waveforms before opening
recognition and returns nothing until both sources validate. Its recovery guard
covers `open()` as well as inference: even a partially initialized first or second
source is reset after failure. Tests require a subsequent capture to succeed on
the same owner. This is component lifecycle coverage, not installed or acoustic
speaker-identification qualification.

Both source-binding probes accept `--refine-evidence` after the optional
`--encoder-threads N` and before the recording paths. It defaults off and the
loaded event reports the selected value. This invokes the recognizer's existing
bounded evidence replay; it does not change text, enable a production separator,
or admit uncertain competition. A recording is still required after the flag.

Evidence replay can also be requested when own-track uncertainty fragments
sufficient confident speech into too little guarded evidence. It still requires
identical ASR conditioning masks, the original duration floor and boundary
guards, and no confident overlap. Insufficient active speech does not request
extra inference. This repairs replay eligibility, not the short-speech floor.

## Native Mac separated-source adapter

On Apple hosts, `aii_multitalker_coreml_separator` loads eight precompiled
`stage-0.mlmodelc` through `stage-7.mlmodelc` directories. The owning package
loader must verify and pin their entire inventory before construction; the
adapter is not a substitute for signed model binding. It downloads nothing,
compiles nothing at runtime and uses stock Core ML/Foundation. The default
configuration is CPU_AND_GPU; CPU_ONLY exists for explicit numerical checks.
Configuration alone is not proof of physical GPU placement.

Stages have fixed `pcm`, optional `state_in`, and `state_out`/`sources` names.
Only 32000 through 80003 samples at 16 kHz are admitted by this currently
qualified finite adapter. Nonfinite/out-of-range PCM, incorrect stage
signatures and output shapes are refused. Outputs use the same normalization
and exact-source evidence binding as the ONNX composition. Calls and `open()`
are serialized by the owner; `cancel()` is concurrent and does not wait on
inference. The current stage retires before cancellation returns from
`separate()`. The owner must await retirement before reset/destruction.

Offline packaging, starting from the pinned upstream source/checkpoint and
independently generated upstream waveform fixtures:

```sh
python -m scripts.export_coreml_separator --source /path/to/upstream \
  --checkpoint /path/to/checkpoint --fixtures /path/to/frozen-fixtures \
  --fixtures-sha256 FIXTURE_MANIFEST_SHA --out /path/to/new-export
python scripts/stage_coreml_separator.py --model /path/to/separator.mlpackage \
  --model-sha256 MODEL_SHA --weights-sha256 WEIGHTS_SHA --out /path/to/new-stages
aii_coreml_compile_stages /path/to/new-stages /path/to/new-compiled
aii_coreml_separator_probe /path/to/new-compiled /path/to/frozen-fixtures gpu
aii_coreml_separator_probe /path/to/new-compiled /path/to/frozen-fixtures cpu
python -m unittest tests.qualify_coreml_stages
python -m unittest tests.qualify_coreml_export
```

The exporter checks clean upstream revision, checkpoint and every reference
waveform hash before and after execution. It applies shape-equivalent width-one
convolutions, hierarchical centered normalization and explicit attention/padding
only to an export copy. Scoped trace overrides are restored on failure.
Parameters are unchanged; the reference is the independently frozen upstream
output, not this copy. All five qualified lengths and a repeated length must
pass the unchanged 2e-4 limit. Successful reports use hashes and package-relative
paths, not recordings. Failed diagnostic logs may contain local paths and must
remain private. Development tool dependencies do not enter the plugin.

The staging tool cuts complete three-block boundaries from the qualified
24-block graph, preserves dependencies, binds output bytes and does not claim
qualification itself. Only referenced FP32 weight blobs are copied into each
stage, with byte-for-byte read-back; unused layers are not duplicated eight
times. The compiler is an offline packaging tool, not linked
into the adapter. Compile on the deployment-compatible OS/toolchain and bind
the resulting files in the signed model inventory. Compilation on a newer Mac
does not qualify older macOS releases. Full conversion is an explicit model
qualification command, separate from model-free CI. Both commands are required;
a source-test pass is not numerical model parity.

`aii_coreml_source_binding_probe` runs the existing recorded source-text and
identity-evidence composition with this adapter. Its separator argument is
the compiled directory; the following ORT-thread/CUDA placeholders must be
`0 -1`. It explicitly warms the model before measuring cancellation; cold
readiness is not included. This is an executable offline component path, not
an installed worker selection, live conversation or broad UID qualification.
Never claim CPU cancellation latency from CPU waveform parity alone.

## Nemotron native composition

An explicit `NemotronConfig` selects Nemotron 3 instead of the legacy diarizer.
Build with `NEMOTRON_ROOT` pointing to the pinned, patched native library; an
unconfigured build still uses the existing four-track implementation. No SDK
change, environment-dependent model selection, or automatic fallback is added.
See [the desktop composition](../../docs/NEMOTRON_NATIVE_DESKTOPS.md) for exact
dependency pins, build steps, evidence and remaining release gates.
