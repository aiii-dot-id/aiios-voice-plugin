# Common native build

This entry point builds the existing ASR, VAD, UID and semantic endpoint
implementations in one dependency graph, against one explicitly supplied ONNX
Runtime. The component source files and numerical settings are unchanged.
Its component contract tests and the new private session owner contracts are
registered together with CTest.

Supply the immutable platform recipe's `ORT_INCLUDE`, `ORT_LIBRARY`,
`UID_FRONTEND_SOURCE`, `UID_KNF_ROOT`, `UID_KISS_ROOT` and endpoint dependencies.
The portable endpoint uses `POCKETFFT_ROOT`, `SLEEF_INCLUDE`, `SLEEF_LIBRARY`;
the separately qualified Windows ATen candidate uses `AII_ENDPOINT_ATEN=ON`
and `ATEN_ROOT`. These must name preverified dependencies. Existing paths are
checked here; path existence is not a substitute for the recipe's hashes.
The build does not fetch dependencies or search for a system Python/runtime.

The private C++ session owner composes real ASR/VAD/endpoint/TTS/UID on all three
desktops and connects through the Go carrier and Plugin SDK. See
[its boundary](session/README.md) and the artifact-specific
[desktop handoff](../../docs/DESKTOP_BETA4_HANDOFF_20260918.md).
Installed browser acceptance remains separate from model and transport proofs.
The existing component targets are shared libraries; mobile source and earlier
component checks are not a qualified app-linked iOS/Android release. Building
on one OS is not qualification on another.

The architecture, capability-preservation rules and executable next milestones
are in [the common runtime plan](../../docs/COMMON_NATIVE_RUNTIME_20260911.md).
