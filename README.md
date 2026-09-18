# AII OS voice plugin

The source of the `id.aiii.voice` plugin for AII OS: a native voice engine
(streaming speech recognition and synthesis, voice activity and turn
detection, speaker identification, full-duplex interruption and recovery)
carried into the host through the AII OS Plugin SDK.

## Install and use

Install through the AII OS plugin catalog; do not run a carrier executable as
a standalone application. The host selects the native companion and downloads
its hash-bound models automatically. No system Python or development checkout
is required on a user's machine.

The prepared next release is **0.1.0-beta.4**, for **AII OS 0.1.8+** on
macOS Apple Silicon, Ubuntu x86-64 and Windows x86-64. Preparation is not
publication: use the catalog's published version until the new assets and
signed catalog have been released together.

- [Desktop beta guide](docs/DESKTOP_BETA.md): features, settings, UID,
  prerequisites, updates and limits.
- [Build and validate](docs/DEVELOPMENT.md): source-only tests and the pinned
  native carrier build.
- [Release procedure](docs/PUBLISHING.md): exact artifacts, signing,
  publication order and installed acceptance.

## Source layout

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

The integrated source checkpoint, reproducible focused gates and remaining
release boundaries are recorded in
[`docs/SOURCE_CLOSEOUT_20260917.md`](docs/SOURCE_CLOSEOUT_20260917.md).
The current native session contract (including output-only speech) is in
[`docs/NATIVE_SESSION_CONTRACT.md`](docs/NATIVE_SESSION_CONTRACT.md); its
implementation evidence is in
[`docs/OUTPUT_ONLY_DELIVERY_20260918.md`](docs/OUTPUT_ONLY_DELIVERY_20260918.md).
The signed beta.4 artifact and its remaining gates are recorded in
[`docs/DESKTOP_BETA4_HANDOFF_20260918.md`](docs/DESKTOP_BETA4_HANDOFF_20260918.md).
A clean source landing is not a replacement signature or installed-product
qualification. Historical evidence audits require the bound external artifacts;
missing evidence is not silently converted into a passing gate.

Licensed under the Apache License 2.0 (see LICENSE). Third-party models,
datasets and papers keep their own terms.
