# Portable audio frontend contract

The audio frontend is one owned implementation with thin platform bindings, not
independent Swift, Kotlin, Python, Go, and Windows feature extractors.

The implementation exposes a stable C ABI so it can be called from Swift or
Objective-C on Apple targets, JNI on Android, native desktop applications, and
the later Go AII OS plugin boundary. The core has no network, UI, credential,
model-download, or platform-policy dependency.

Each transform is driven by a content-addressed manifest declaring:

- input PCM type, byte order, channels, and sample rate;
- channel mixing and amplitude normalization;
- resampling kernel, boundary handling, and delay;
- pre-emphasis, framing, window, padding, and terminal flush;
- FFT/power computation, mel bank, log floor, and normalization;
- output axes, shape, element type, state layout, and lookahead;
- numerical mode and tolerance policy.

The streaming ABI has five lifecycle operations: create from immutable
manifest bytes, push ordered PCM, flush terminal input, reset state, and
destroy. Push returns only features whose declared future context exists.
Flush may emit the final padded frames. Reset returns byte-identical behavior
to a fresh instance.

Golden bundles carry source audio, normalized and resampled PCM, intermediate
frames, and final features. Streaming state remains opaque behind the C ABI;
one-shot and arbitrarily chunked execution must agree under the frozen
tolerance, and reset must match a fresh instance. Exact chunking, lookahead,
provider, fallback, and terminal behavior remain visible without pretending an
unimplemented state-export surface exists.

Model-specific feature manifests are allowed; model-specific implementations
are not the default. A vendor-native frontend may compete only by reproducing
the same manifest and golden intermediates. Core ML, LiteRT, ONNX Runtime, MLX,
CUDA, Metal, and DirectML remain inference backends rather than authorities for
audio semantics.

## Current executable implementation

The first project-owned implementation is in `runtime/audio_frontend`. It is a
dependency-free C99 core with a stable ABI and one independent NumPy oracle.
It accepts a canonical ordered ASCII manifest rather than a platform object or
language-specific configuration. Every line is exactly `key=value`, every key
appears once in the frozen order, the file is newline terminated, and unknown,
missing, reordered, non-ASCII, carriage-return, and NUL-bearing input is
refused. The full bytes are the content-addressed authority.

The current transforms are intentionally narrow and exact:

- one-channel, little-endian signed 16-bit PCM at either 16 or 48 kHz;
- amplitude scale `1/32768` and no pre-emphasis;
- identity processing at 16 kHz, or project-owned 48-to-16 kHz causal
  windowed-sinc resampling with a 63-tap symmetric-Hann kernel, 0.94 Nyquist
  cutoff ratio, and 31-input-sample declared group delay;
- 400-sample causal frames, 160-sample hop, periodic Hann window;
- 512-point power spectrum, 80 HTK mel bins over 20-7600 Hz;
- `log1p`, no utterance normalization, float32 frame-major output;
- no future lookahead; terminal flush right-zero-pads every remaining hop.

`resampling=none` still requires equal input and processing rates; it is not
permission for platform bindings to invent their own resampler. The only
non-identity transform currently admitted is the manifest-bound 48-to-16 kHz
path. Its output cardinality is `ceil(input_samples * 16000 / 48000)`, its left
boundary is zero-filled, and terminal flush emits no synthetic resampler tail.
Capture rates other than 16 or 48 kHz remain refused until another transform
is added to this same authority and golden surface.

The ABI exposes non-mutating frame-capacity queries. Insufficient output
capacity consumes no input. Push after flush and a second flush are refused;
reset makes the next run byte-identical to a fresh instance. The core buffers
at most one processing frame internally; its workspace does not grow with the
size of a caller's input chunk. Output remains caller-owned and explicitly
sized by the capacity query.

The committed golden bundles at `eval/conformance/audio-frontend/` bind each
exact manifest, source PCM, normalized PCM, resampled PCM, framed/padded PCM,
and final features by full SHA-256. `scripts/validate_audio_frontend_bundle.py`
checks every binding, recomputes all intermediates through the independent
oracle, and can compare a compiled native library under the manifest's frozen
tolerance. One-shot and arbitrary capture chunking produce byte-identical
native features. A separate filter test requires a 12 kHz input tone to emerge
at less than one percent of a 1 kHz reference after 48-to-16 kHz conversion.

This establishes frontend and resampling semantics, a bounded synthetic
anti-alias check, and numerical conformance only. It does not establish
microphone behavior, perceptual resampling quality, model quality, latency,
energy, sustained thermal behavior, or support on a physical target.
