# Nemotron native desktop composition

Scope: macOS Apple Silicon, Ubuntu Linux and Windows desktop qualification.
This is an implemented native recognition component with an opt-in sealed
macOS candidate. It does not change the default public release.

## Composition

Nemotron 3 diarization runs in NVIDIA's NeMo-Speech.cpp runtime. Its eight
10 ms activity channels condition the existing Parakeet streaming RNNT graphs.
Each speaker has its own encoder and decoder state. No transcript is assigned
to a person merely because that person's voice is present somewhere in a mix.

The native diarizer accepts raw mono 16 kHz PCM. ASR uses its existing mel
frontend and ONNX Runtime. The encoder can explicitly select CUDA; the
preencoder, decoder and joiner remain on CPU. Nemotron uses Metal on macOS and
Vulkan on the tested Ubuntu NVIDIA device. This is not an all-GPU pipeline.
There is no Python inference service, SDK fork, or public control added here.

Three correctness details are necessary, not optional tuning:

1. Average disjoint groups of eight 10 ms predictions for the ASR 80 ms masks;
   exclude padding in a partial group. UID instead consumes the original
   10 ms predictions, so brief overlap is not hidden by averaging.
2. The first ASR chunk has 112 feature frames and nine zero cache frames;
   drop two preencoded frames on every chunk, including the first. The old
   first-chunk convention shifts the Nemotron masks and loses overlap words.
3. Never gate away a not-yet-active speaker's acoustic context. All dormant
   tracks have identical zero foreground and union-of-other-speakers background.
   Compute that common state once, then copy it before the first differing
   mask. `--nemotron-reference` computes all eight tracks independently to
   test this optimization against an ungated reference.

`Microphone` owns the single-threaded native stream. Control may set the
existing atomic cancellation fence without destroying that stream during
inference. The inference owner must retire before reset/destruction.
Finishing an utterance permits a next utterance with bounded AOSC speaker
memory retained; a new session resets it. This does not turn a track number
into a durable named person or a calibrated identity confidence.

## Pinned dependencies

| Input | Binding |
| --- | --- |
| NVIDIA/NeMo-Speech.cpp | `97a15afa5caa9bce5baaa86c1184103877af4101` |
| ggml | `c03b4e2bcece5134827881af90242086daf75be5` |
| SentencePiece | `17d7580d6407802f85855d2cc9190634e2c95624` |
| NeMo reference used for pooling and ASR chunking | `f613eed86ed4696db0891aac4e9104337a39142c` |
| Nemotron source checkpoint SHA-256 | `867c53f552998f772e5b5e5c082962ae85ee7ca5669c2bc17d7f615133d4e96d` |
| Converted GGUF SHA-256 | `3145e9285b9625ae3bbf2833255772de725f73abe90e63b61dc50336c89e03f6` |

The GGUF is 395,298,816 bytes. The F32 conversion contains one F16 tensor;
do not describe it as exclusively F32. Model artifacts are not in this repo.

Apply `runtime/native_multitalker/patches/nemo-utterance-and-consumption.patch`
to the pinned native checkout before building (patch SHA-256
`edb8b0e39e1dee331a9689253894dc1ebfba432ebc5c18f6faf484f4e00aaff3`).
It adds four bounded C APIs:
start the next utterance while retaining speaker memory, read a prediction
range, discard consumed predictions, and copy a stream
(`nemo_speech_diar_stream_clone`). Discard affects output history, not
the bounded AOSC model cache. Segment queries after discard describe only
retained history. Both platforms test exact prediction equality with and
without discard, and new-session isolation.

The copy duplicates every per-stream value (AOSC speaker cache and FIFO,
audio and mel buffers, clocks, retained predictions, any resampler history)
and shares only the model's weights, backends and frontend; the stream holds
no tensors or backend buffers of its own. Bounded evidence refinement replays
on such a copy, so the live stream is never fed. Release the copy with
`nemo_speech_diar_stream_close`; the model must outlive both, and the copy is
used only sequentially, on the thread that owns its source.
`aii_nemotron_lifecycle_probe` checks, with the real model, that a copy taken
mid-utterance or after finish produces exactly its source's predictions for
the same later input, and that feeding a copy never changes its source.

Build the native dependency with its `scripts/configure.sh metal-diar` or
`vulkan-diar` preset and its pinned static SentencePiece helper. For Metal,
apply the upstream preset's ggml Metal patches; do not copy those changes
into the Vulkan build. Install the result into a private staging prefix.
Do not require a developer's Homebrew or system Python at inference time.
A sealed checkpoint whose NeMo library predates the current patch is rebound
with `python -m scripts.rebuild_native_checkpoint --nemo FILE --nemo FILE`,
naming both images of the rebuilt library (see
[DEVELOPMENT.md](DEVELOPMENT.md)). The candidate inherits no execution
evidence and records both images as changed.

### Desktop dependency isolation

On Linux, compile ggml into the Nemotron shared library with
`-DBUILD_SHARED_LIBS=OFF -DCMAKE_POSITION_INDEPENDENT_CODE=ON` and
`-DCMAKE_SHARED_LINKER_FLAGS=-Wl,--exclude-libs,ALL`. Enable the selected
backend explicitly (`GGML_VULKAN=ON` for Vulkan). The component configure gate
refuses a Nemotron implementation with visible or unresolved `ggml_*` symbols:
otherwise ELF can silently resolve them to a different ggml embedded in TTS.
A working conversation alone does not prove independent backend binding.

Windows must include its selected backend DLL (including `ggml-vulkan.dll`
for Vulkan) beside the worker. Keep the bound Microsoft C++ runtime; an
upstream dependency's install directory may contain an older redistributable
that must not overwrite it. ONNX headers, import library and deployed DLL must
have the same bound version. Test executables in separate output directories
need the same DLL closure; PATH alone does not override a System32 DLL.

The native echo wrapper adapts the pinned AEC3 build for MSVC and applies its
auditable vector-access patch to a verified build-tree copy. Its source
checkout is not modified. Echo tests retain the controlled reduction, exact
sample-ordering and short-tail checks on each desktop.

Build this component with explicit `ORT_INCLUDE`, `ORT_LIBRARY` and
`NEMOTRON_ROOT` paths:

```sh
cmake -S runtime/native_multitalker -B /tmp/voice-native-build \
  -DCMAKE_BUILD_TYPE=Release \
  -DORT_INCLUDE="$ORT_INCLUDE" -DORT_LIBRARY="$ORT_LIBRARY" \
  -DNEMOTRON_ROOT="$NEMOTRON_ROOT"
cmake --build /tmp/voice-native-build
ctest --test-dir /tmp/voice-native-build --output-on-failure --no-tests=error
```

On the tested Ubuntu NVIDIA Vulkan path, bind `GGML_VK_DISABLE_F16=1` to
the engine process. Default precision produced a false activity gap in the
fixed recording; full precision removed it. The worker binds this before model
initialization independently of TTS placement: CPU speech generation must not
silently restore half-precision diarization. The environment contract tests both
CPU and Vulkan TTS selection. Device indices are local discovery
results, not portable constants. No driver, global environment or live identity
configuration is changed by the component.

The ONNX graph sessions block their idle worker pools instead of busy-waiting.
Graphs, optimization level, two-thread execution and numerical inputs are
unchanged. All three ONNX environments explicitly disable runtime telemetry.
On the Ubuntu repeat, this reduced the five-recording inference total from
172.5154 to 92.2735 seconds for 138.919 seconds of audio, with identical tokens
and selected evidence. This is one shared-laptop comparison, not a controlled
performance guarantee or proof of stability under arbitrary load.

### Bound encoder thread budget

The sealed `asr_execution` Nemotron profile can optionally declare
`encoder_threads` as an integer from 1 through 16. Omission preserves two
threads. This is runtime packaging metadata, not an identity-call parameter
or a new SDK setting. Unknown fields, booleans, fractional counts, duplicate
keys, and out-of-range counts are refused. The worker passes the declaration
to the encoder; other graph pools and GPU selections are unchanged.

The source-binding diagnostic accepts `--encoder-threads N` immediately after
its output-directory argument and reports the selected count. This permits
paired timing qualification without recompiling different arithmetic or
changing a live identity. A faster count is not automatically selected on
other hardware, and must fit the packaged resource declaration.

In the private Mac recorded-source comparison, eight threads preserved exact
tokens, activity values and selected evidence on 18 waveforms. Four subsequent
whole-composition runs in 2/8/8/2 order also preserved every transcript,
selected waveform and UID decision. Median recognition times were about
3.99/1.79/1.82/3.95 seconds respectively. Separation still dominates the combined
path; these results do not meet an interactive release gate or repair the two
unresolved quiet-speaker cases. No existing installed profile was changed.

The macOS test also exposed a shutdown crash in the previous ONNX library's
Microsoft telemetry worker. Do not promote that binary merely because it had
finished writing a valid transcript: its process exited abnormally. The
replacement candidate is the official `onnxruntime-osx-arm64-1.24.2.tgz` from
Microsoft's GitHub release, archive SHA-256
`0af4fa503e8ea285245b47ee42d0a7461b8156a81270857da0c1d4ecf858abde`,
library SHA-256
`87df6f94dd559ea958748adc80fd4c46d91c52bc025771f513291d155539590a`.
It is tested separately; no installed runtime was overwritten. The replacement
library has only Apple/system dynamic dependencies. Its model execution remains
CPU here; linking CoreML does not prove CoreML execution.

## Measured checkpoint, 2026-09-25

The same fixed seven public read-speech fixtures ran on Apple Silicon/Metal
and physical Ubuntu 24.04/NVIDIA Vulkan. Both passed the existing per-case
thresholds and produced the same error counts: 4 word errors / 258 reference
words (1.55% aggregate permutation word error), with both speakers emitted in
all five two-speaker cases. One word is still missing in the simultaneous-start
case. This is a small regression set, not a broad accuracy claim.

The all-overlap simultaneous-start case provides zero clean voiceprint audio,
as required. It must not create an enrolled identity from mixed audio. The
composition bounds captured PCM and discards consumed predictions. Separately,
both native diarizers processed ten minutes of audio unpaced, tested illegal
lifecycle transitions, preserved discard parity and reset speaker state for a
new session. This was not a ten-minute real-time soak.

The earlier cold-overlap failures remain part of the private evidence. They
were not removed from the panel or reclassified as successful tests.

The final no-spin builds repeat the seven-case gate on both desktops; the
macOS repeat uses the official replacement ONNX library and retires normally.
Its fresh-session and cancellation-recovery checks pass too. The complete
native session contract build passes 45 tests; the standalone component has
four model-free contracts and the acceptance tool had two model-free tests.
An unchanged legacy recognition fixture retains identical tokens and selected
evidence; the replacement ONNX binary changes its printed activity probabilities
by at most approximately 0.000001. These regression checks do not qualify live playback.

The Ubuntu CPU-encoder baseline had inadequate headroom on the simultaneous-start fixture:
9.12 seconds of inference for 8.01 seconds of input, including padding. Other
overlap cases take 17.44–18.88 seconds of inference for 20 seconds of audio.
Startup is measured separately. Those results remain the preserved baseline.

## Explicit CUDA encoder checkpoint

`EncoderExecution` selects CPU by default or a specific CUDA device. An
unavailable requested CUDA provider throws; there is no retry on CPU. This
internal component option does not change the installed plugin's default.
TF32 is disabled and cuDNN uses heuristic convolution selection without its
maximum-workspace override. Idle spinning remains disabled.

CUDA arenas grow by the actual allocation request (`kSameAsRequested`), not
the next power of two. On a shared 4 GiB laptop, the default growth exhausted
device memory during model loading before a second cuDNN handle could create
its streams. In the paired recorded-speech probe, exact growth reduced peak
device use from 3,763 MiB to 3,112 MiB and completed inference with unchanged
weights and math precision. This is a measured configuration, not a universal
memory ceiling. The seven-case overlap gate also passes with explicit CUDA
encoder placement; fresh bound-carrier session qualification remains separate.

The composition proof binds native libraries from `lib` by default. Windows
uses `--native-library-subdir bin`; put the probe beside the candidate's exact
DLLs, so Windows does not choose an older system ONNX Runtime first. Both
layouts have binding tests, including missing libraries and changed bytes.

ONNX Runtime assigns integer shape calculations and Boolean attention masks
to CPU. Disallowing every CPU node prevents this graph from loading; that
failed attempt is retained. The acceptance tool instead profiles actual
execution and requires encoder MatMul, Conv, LayerNormalization and Softmax
on CUDA. Every CPU node must be a recognized bookkeeping operation with
integer/Boolean input and output types. Missing metadata, unknown placement,
floating-point CPU work or missing neural CUDA execution fails the gate.
Provider registration alone is never evidence of hardware execution.

The physical Ubuntu 24.04 laptop's 4 GB RTX 3050 passed all seven fixtures
with CUDA encoding and Vulkan diarization. Tokens and selected UID evidence
are exactly equal to the CPU baseline in every case; errors remain 4/258.
The run includes profiler overhead:

| Case | Input seconds | CUDA encoder composition inference seconds |
| --- | ---: | ---: |
| Solo A | 7.025 | 2.550 |
| Solo B | 7.010 | 2.340 |
| Alternating | 19.025 | 5.087 |
| Equal overlap | 20.010 | 5.272 |
| Second speaker quieter | 20.010 | 5.335 |
| First speaker quieter | 20.010 | 5.481 |
| Simultaneous start | 8.010 | 3.140 |

Fresh-session isolation and between-utterance cancellation/reset recovery also
pass on CUDA. These checks are not an installed barge-in test. The component
now has five model-free contracts, including invalid device/thread settings
and unavailable-CUDA refusal on the CPU-only runtime; the complete native
session contract suite has 46 passing tests. The acceptance tool has three
model-free tests, including hostile placement-profile cases.

The CUDA runtime is Microsoft's official ONNX Runtime 1.24.2 Linux x64 GPU
archive, SHA-256 `bcb42da041f42192e5579de175f7410313c114740a611e230afe9d79be65cc49`;
its main library is `4f79ab81eda5f5ef3bf4b280cb4aed6eb96f30153ae9a5593735a4bddacab74e`.
The isolated test dependency tree uses NVIDIA cuBLAS 12.9 (build 2.10), cuDNN 9.26 (build 0.51),
CUDA runtime/NVRTC 12.9.79/12.9.86, cuFFT 11.4 (build 1.4), cuRAND 10.3 (build 10.19) and
nvJitLink 12.9.86. This staging tree is approximately 2.7 GB installed, before
ORT and models: it is not yet a minimized distribution. Python/pip downloaded
these native libraries for the test; Python is not used for inference and must
not become an end-user prerequisite. The qualification binds 202 input files,
including the runtime trees, and checks those bindings again after inference.
No driver or system library was installed or changed.

The updated component also repeats all seven macOS fixtures with the official
CPU ONNX runtime and Metal diarizer. Tokens and selected evidence remain
identical to its preceding checkpoint, with normal process retirement.

Use `--encoder-cuda DEVICE` and repeatable `--encoder-runtime DIRECTORY` with
`scripts/prove_nemotron_composition.py` to run the profiled gate. Each directory
is hashed recursively. These private profiles contain graph names and tensor
shapes, not a portable public acceleration declaration. The production loader
still needs a sealed dependency inventory and device/resource admission.

## Next delivery gates

- Bind this recognizer and native libraries into the sealed runtime, with
  platform device selection, hashes, complete redistributable dependencies,
  supported OS/CPU floors and unchanged cancellation/receipt contracts.
- Test the real session, TTS, interruption, recovery and installed browser path;
  model-component tests cannot substitute for those checks.
- Resolve persistent-gallery ambiguity against approved recordings without
  silently deleting, merging or relabeling existing profiles. Native track
  continuity does not repair conflicting historical voiceprints.
- Qualify longer sessions, different microphones, new speakers, quieter
  overlap, resource contention and observed latency. The streaming chunk's
  lookahead remains even when inference runs faster than real time.

No release/catalog update, broad UID qualification, per-word timing/confidence,
mobile qualification or Windows qualification is claimed by this checkpoint.

## Sealed macOS candidate

The native session factory accepts the following **sealed runtime profile**,
not an operator or tool-call setting:

```json
{"asr_execution":{"diarizer":"nemotron","gpu":0,"encoder_cuda":-1}}
```

The GPU index selects the native diarizer device; `encoder_cuda: -1` selects
the CPU ONNX encoder on macOS. Both device indices are checked integers.
Unknown fields, an unsupported diarizer, missing bound `nemotron.gguf` under
the ASR model root, or unavailable declared hardware refuse activation. An
omitted execution profile preserves the existing recognizer. It does not
silently substitute another diarizer when Nemotron is requested.

`scripts/stage_nemotron_macos.py` stages this profile from a hash-bound parent,
copies the native dependency closure as regular files, relocates/signs native
images, and rebuilds the carrier against the resulting inventory. It replaces
only hearing components and preserves the parent's other model bytes. Model
files and native dependencies remain separate, inventoried artifacts; no
system Python or package manager is required for inference. Embed Metal's
shader library and strip local compiler source paths when building dependencies.
Vendor-provided diagnostic build paths are distinguished from our private data.

The complete native contract suite passes 46 tests with this factory wiring.
A separately signed macOS candidate passed the isolated real-host installation
gate with recorded microphone input: enrolled speaker attribution, synthesis,
worker termination, stalled-work detection, restart and recovery. The gate
uses the real installer and containment with a test trust root and a simulated
audio sink. It is not live browser playback or broad speaker-accuracy proof.
