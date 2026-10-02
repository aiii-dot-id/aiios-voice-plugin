# Portable audio frontend

This directory contains the single project-owned audio preprocessing authority.
The C99 core has no model framework, network, UI, credential, download, or
platform-policy dependency. Swift/Objective-C, Kotlin/JNI, Go, and desktop
bindings must call this ABI rather than reimplementing its math.

Build a local shared library from the workspace root:

```sh
python - <<'PY'
from pathlib import Path
from runtime.audio_frontend.native import build_shared_library

build_shared_library(Path(".build/libaiii_voice_frontend.dylib"))
PY
```

On Linux, use a `.so` output name. The C source itself is written against C99.
Define `AIII_VOICE_FRONTEND_BUILD` when building a Windows DLL and
`AIII_VOICE_FRONTEND_STATIC` when linking it statically; consumers otherwise
import the DLL surface. A Windows build and physical-target conformance result
have not yet been run.

Conformance is checked against golden source-to-feature bundles, by the NumPy
oracle and by a compiled core.

The 48 kHz manifest uses the core's owned causal 63-tap windowed-sinc path to
produce 16 kHz processing samples. No platform binding may silently substitute
a system resampler. The conformance reports say `latency_claims: false`: these are
deterministic numerical conformance results, not live microphone, resource,
device, perceptual-quality, or latency results.

`runtime/audio_frontend/tools/aiii_voice_frontend_runner.c` is the thin native
file runner used for physical executable conformance. It contains no DSP. A
driver compiles or invokes that runner, compares its output with the golden
bundles, records exact source/binary/bundle identities, and keeps the result at
`numerical_executable_only`. Running the runner does not qualify microphone or
live behavior.

See `spec/PORTABLE_AUDIO_FRONTEND.md` for the frozen manifest grammar,
transform, lifecycle, refusal behavior, and explicit remaining gaps.
