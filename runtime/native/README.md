# Common native build

This entry point builds the existing ASR, VAD, UID and semantic endpoint
implementations in one dependency graph, against one explicitly supplied ONNX
Runtime. Component and session contract tests are
registered together with CTest.

Supply the immutable platform recipe's `ORT_INCLUDE`, `ORT_LIBRARY`,
`UID_FRONTEND_SOURCE`, `UID_KNF_ROOT`, `UID_KISS_ROOT` and endpoint dependencies.
The portable endpoint uses `POCKETFFT_ROOT`, `SLEEF_INCLUDE`, `SLEEF_LIBRARY`;
the Windows ATen backend uses `AII_ENDPOINT_ATEN=ON`
and `ATEN_ROOT`. These must name preverified dependencies. Existing paths are
checked here; path existence is not a substitute for the recipe's hashes.
The build does not fetch dependencies or search for a system Python/runtime.

The internal C++ session owner composes real ASR/VAD/endpoint/TTS/UID on all three
desktops and connects through the Go carrier and Plugin SDK. See
[its boundary](session/README.md).
Component targets are shared libraries. Native desktop builds do not produce
a mobile application; validate each target platform separately.
