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

This is **not** the complete common voice engine. The private C++ session owner
now composes real ASR/VAD/endpoint/TTS on Mac and its model-free contracts run on
Mac, Ubuntu, Windows and Pixel. See [its boundary](session/README.md). Complete
SDK/profile/UID integration, provider placement, release packaging and mobile
linkage still need qualification. The existing component targets are shared libraries;
this does not yet implement the intended app-linked iOS build. Building on one
OS is not qualification on another.

The architecture, capability-preservation rules and executable next milestones
are in [the common runtime plan](../../docs/COMMON_NATIVE_RUNTIME_20260911.md).
