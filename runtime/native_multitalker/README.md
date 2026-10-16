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
