# AII OS voice plugin

AII Voice (`id.aiii.voice`) provides local speech recognition and synthesis,
voice activity and turn detection, speaker identification, and interruption
through the AII OS Plugin SDK. Desktop packages support macOS Apple Silicon,
Linux x86-64, and Windows x86-64.

## Install and use

Install from the AII OS plugin catalog. The host selects a compatible Full,
Small, or CPU-only runtime and downloads its required models. Open Speech
settings to choose the plugin, speaking voice, language, and turn pause.
User installation needs no Python, SDK checkout, or development tools.
Use the host and plugin versions offered together by the signed catalog.

See the [desktop guide](docs/DESKTOP_BETA.md) for settings, speaker enrollment,
updates, and known limits. Speaker labels do not authenticate a person or
confer permission. Overlapping speech, short recordings, noise, and changed
microphones can leave attribution unresolved.

## Build

The [native build](plugin/NATIVE_BUILD.md) names the pinned Plugin SDK revision
and how each carrier and its runtime are built.
[Development instructions](docs/DEVELOPMENT.md) cover dependencies and tests.
[What changed in 0.1.0-beta.12](docs/CHANGES_0.1.0-beta.12.md) lists every
change since 0.1.0-beta.11, by subject.
The shipped engine is a C++ worker launched by a Go carrier. Python files are
build tools, tests, and reference implementations; they are not installed in
the native plugin package.

- `plugin/native`: Go carrier, tool schemas, settings, and runtime selection.
- `runtime/native*`: native engine components and platform bindings.
- `scripts`: build, model preparation, and packaging tools.
- `spec`: schemas and test vectors.
- `docs`: user instructions and current implementation contracts.

The [session contract](docs/NATIVE_SESSION_CONTRACT.md) and
[carrier/worker protocol](docs/NATIVE_WORKER_WIRE.md) describe the native
boundary. Models and native runtime archives are separate release assets;
the package declares their exact hashes and resource requirements.

Copyright (c) AI Identity Incorporated. Licensed under Apache-2.0; see LICENSE.
Required third-party notices and component licenses remain with their sources
and release packages.
