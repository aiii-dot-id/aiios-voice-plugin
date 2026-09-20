# Native speaker-conditioned recognition component

This is a development component for the required speaker-aware plugin upgrade.
It is **not selected by the released worker**, and is not a qualified plugin,
complete microphone recognizer, enrolled-person matcher, or mobile runtime.

The encoder owns separate channel, temporal and valid-length caches for up to
four anonymous acoustic tracks. The RNNT decoder owns a separate last token and
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

The ONNX adapter is explicitly CPU-only development code. It does not pretend
to have qualified CUDA, DirectML, CoreML, mobile GPU or NPU placement. Production
asset sealing, accelerator policy and the resident session must be connected
before selecting this component in a package. Existing TTS, VAD, interruption
and playback-receipt behavior has not been replaced.

## Checks

Model-free state contracts:

```sh
cmake -S runtime/native_multitalker -B .build/multitalker -DCMAKE_BUILD_TYPE=Release
cmake --build .build/multitalker
ctest --test-dir .build/multitalker --output-on-failure --no-tests=error
```

Supplying explicit `ORT_INCLUDE` and `ORT_LIBRARY` also builds the ONNX adapter
and recorded-trace probe. The reproducible graph, trace and proof commands are
in [the measured checkpoint](../../docs/NATIVE_MULTITALKER_RESULT_20260920.md).
Private recordings/features and generated models never belong in this directory.
