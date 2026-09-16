# AII OS voice plugin

The source of the `id.aiii.voice` plugin for AII OS: a native voice engine
(streaming speech recognition and synthesis, voice activity and turn
detection, speaker identification, full-duplex interruption and recovery)
carried into the host through the AII OS Plugin SDK.

- `plugin/native` — the Go carrier the host launches: the session lane,
  enrollment operations, settings, runtime inventory and readiness.
- `plugin/integration` — the host-side integration proofs.
- `runtime` — the engine: the C++ core and its platform bindings under
  `runtime/native*`, the audio frontend, and the reference implementations.
- `spec` — the voice core protocol and the artifact contracts.
- `docs` — the wrapper design, the Beta 1 delivery contract, guided
  enrollment, operator settings and the common native runtime.
- `scripts` — the carrier build and the package assembly.

Build the carrier against the pinned Plugin SDK revision named in
`plugin/sdk-source.json` (see `plugin/NATIVE_BUILD.md`). Models and runtime
companions are release assets, named by digest in the manifests here; they
are not source and do not live in this repository.

Licensed under the Apache License 2.0 (see LICENSE). Third-party models,
datasets and papers keep their own terms.
