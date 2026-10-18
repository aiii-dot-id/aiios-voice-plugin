# Nemotron native desktop composition

## Composition

Nemotron 3 diarization runs in NVIDIA's NeMo-Speech.cpp runtime. Its eight
10 ms activity channels condition the existing Parakeet streaming RNNT graphs.
Each speaker has its own encoder and decoder state. No transcript is assigned
to a person merely because that person's voice is present somewhere in a mix.

The native diarizer accepts raw mono 16 kHz PCM. ASR uses its existing mel
frontend and ONNX Runtime. The encoder can explicitly select CUDA; the
preencoder, decoder and joiner remain on CPU. Nemotron uses Metal on macOS and
Vulkan on Linux. This is not an all-GPU pipeline.
There is no Python inference service in this composition.

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
retained history. Verify prediction equality with and without discard,
and new-session isolation, for each target build.

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
into the Vulkan build. The shipped Metal build sets
`GGML_METAL_EMBED_LIBRARY=OFF` and carries precompiled kernels. Install the
result into a separate staging prefix.
Do not require a developer's Homebrew or system Python at inference time.
A sealed checkpoint whose NeMo library predates the current patch is rebound
with `python -m scripts.rebuild_native_checkpoint --nemo FILE --nemo FILE`,
naming both images of the rebuilt library (see
[DEVELOPMENT.md](DEVELOPMENT.md)). Re-run the native tests whenever either image changes.

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

The worker sets `GGML_VK_DISABLE_F16=1` before model initialization,
independently of TTS placement. Device indices come from local discovery.
ONNX graph sessions block idle worker pools instead of busy-waiting. All
three ONNX environments explicitly disable runtime telemetry.
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
