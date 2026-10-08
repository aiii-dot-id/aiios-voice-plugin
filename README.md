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

Published desktop prereleases run on macOS Apple Silicon, Ubuntu x86-64 and
Windows x86-64. This source tree also contains changes for the next
candidate; source main is not an installable release.
Use the version and compatible host range offered by the signed catalog and
the assets on the matching GitHub release. Do not infer compatibility from
the host's version number alone when a capability landed after that release.

- [Desktop beta guide](docs/DESKTOP_BETA.md): features, settings, UID,
  prerequisites, updates and limits.
- [Build and validate](docs/DEVELOPMENT.md): source-only tests and the pinned
  native carrier build.
- [What changed in the source for 0.1.0-beta.11](docs/CHANGES_0.1.0-beta.11.md):
  every change since the last release, by subject, with what was shown and
  what was not.

## Source layout

- `plugin/native` — the Go carrier the host launches: the session lane,
  enrollment operations, settings, runtime inventory and readiness.
- `runtime` — the engine: the C++ core and its platform bindings under
  `runtime/native*`, the audio frontend, and the reference implementations.
- `spec` — the voice core protocol and the artifact contracts.
- `docs` — the session and speaker contracts, guided enrollment, settings,
  speaking languages and the correction list.
- `scripts` — the carrier build and the package assembly.

The engine that ships is native: the C++ worker and the Go carrier that
starts it. The carrier starts only the native worker its bound runtime profile
names, and refuses at its start a profile that describes an interpreter. The
Python in this tree is tests and tooling: the test suite, the scripts that
build, stage and assemble a release, and a Python engine
(`runtime/plugin_engine` and the Python packages it imports) that is kept as a
double of the native worker for the tests. Released packages hold no Python
(since 0.1.0-beta.7, see `plugin/NATIVE_BUILD.md`), and no script here packs
the double.

Build the carrier against the pinned Plugin SDK revision named in
`plugin/sdk-source.json` (see `plugin/NATIVE_BUILD.md`). Models and runtime
companions are release assets, named by digest in the manifests here; they
are not source and do not live in this repository.

The native session contract (including output-only speech) is in
[`docs/NATIVE_SESSION_CONTRACT.md`](docs/NATIVE_SESSION_CONTRACT.md). The
speaker-aware hearing and continuity work is described in
[`docs/SPEAKER_CONTINUITY.md`](docs/SPEAKER_CONTINUITY.md).
Source commits do not alter a previously signed package. A new release needs
its own signature, exact host compatibility, installed-product tests and
public catalog entry. Missing evidence is never a passing gate.

Licensed under the Apache License 2.0 (see LICENSE). Third-party models,
datasets and papers keep their own terms.
