# Native full-context speaker embedding

Private C++17/C ABI component for the voice engine, not a new public SDK API.
It replaces the Python inference owner for the **same** WeSpeaker checkpoint
and frontend. Python remains the independent build/evaluation harness only;
the library/probe has no CPython or Torch linkage and runs with an empty PATH.
No checkpoint, model, enrollment, identity or installed plugin is replaced.

## Bound assets and ownership

- Model: `voxblink2_samresnet34_ft.onnx`, 100,865,597 bytes, SHA-256
  `33af8affe6191b1ebd196d2b56e22c2934104cd2764abfdbdd954d3a934eb2a1`.
  The existing asset owner verifies it before calling `aii_uid_create`.
  Creation retains a private copy; releasing the caller's buffer is proven.
  File size and graph geometry are checked here, not substituted for hashing.
- The existing `speaker_identity/native` frontend retains Kaldi-native-fbank
  1.22.3/KissFFT, 80 mel bands, whole-utterance CMN, 400-sample frames and
  160-sample hops. There is no two-second crop, resampling or padding.
- Native proof: macOS arm64, ONNX Runtime 1.29.0, CPU, two inference threads.
  The exact library, headers, source, model, PCM, policy and outputs are bound
  in the proof. The inspected header API is 1.24: an older ORT must not be
  substituted without compatible headers and a new complete proof.
- `ORT_DISABLE_TELEMETRY=1` is required before runtime initialization. The
  shipping runtime recipe still needs telemetry excluded at build time.
- Backend selection is explicit. `cpu` is the proven reference. `cuda`
  requires the CUDA provider; absence is refused, not silently changed to CPU.
  CUDA registration code is **not** GPU node-placement or performance proof.
  DirectML, CoreML and the other target bindings are not implemented here.

## Caller contract

`aii_uid_create`, `aii_uid_embed`, `aii_uid_cancel_through`, `aii_uid_phase`
and `aii_uid_destroy` are declared in `uid.h`.

One background embedding worker per owner consumes a complete immutable mono
16 kHz PCM16-LE utterance: 31,920–480,000 samples (1.995–30 seconds). Too-short,
odd-sized, oversized, wrong-rate, silent or excessively clipped inputs are
refused. They are not made acceptable by padding or truncation. Utterance IDs
are positive and strictly increasing when consumed. A concurrent worker is
refused, not queued behind inference.

Success produces a 256-dimensional float64 unit vector, preserving the current
enrollment representation. The output buffer is untouched on refusal, fault or
cancellation. Return codes: 0 success, 1 invalid, 2 unusable signal, 3 cancelled,
4 fault, 5 busy. A model-run fault poisons the owner and requires recreation.
No C++ exception is allowed to escape the C ABI.

Cancellation can run on the independent control thread. It monotonically
fences IDs through the supplied value and requests termination of that active
ORT run. A short mutex protects the run-options lifetime and vector publication;
it is never held across frontend extraction or model inference. Cancellation
admission and actual worker retirement are separate measurements. The pinned
ORT termination status alone is classified as cancellation; an unrelated
model error racing the request remains a fault. An unknown runtime diagnostic
fails closed.

`phase` is an atomic observation (idle/frontend/inference), not a completion
receipt or proof of which GPU kernel executed. Destroy only after all worker
and control callers retire. The session owner must additionally fence any
previously fetched vector by session/utterance before publishing an observation.
Never run full-utterance embedding on the microphone or playback-stop thread.

## Classification remains in the existing owner

This component returns embeddings. It does not mint identity, enroll speakers,
lower thresholds, persist a second policy, declare a transcript final or open
an audio device. Existing snapshot/policy ownership and transcript-sequence
binding remain authoritative. A late speaker result amends its transcript; it
must not start a new conversational turn. The agreed host UID seams follow
the private CP1 install; this library is not added to the frozen CP1 package.

## Evidence and remaining integration

[Full native proof](../../deliverables/native-uid-cpp-20260911-r3/README.md):
161 utterance embeddings and all 125 frozen speaker decisions carry over.
Five further process lifecycles and four compiled separating mutations check
cancellation, prompt retirement, fault classification and full-context use.
The separate instrumented build is recorded with the same numerical/decision
gate, not an assumed bit-identical build.

This is a native UID component, not a completed native resident or human-level
UID qualification. The unchanged harder known-speaker subset still rejects
2/20 examples. Native GPU/target qualification, live/noisy/overlap evidence,
session composition and the signed browser journey remain required. Keep the
working GPU implementations and enrolled vectors intact until replacement
passes those gates. Native semantic endpointing must also preserve the current
neural turn decision; VAD alone is not an equivalent substitute.
