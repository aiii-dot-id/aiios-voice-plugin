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

Validate the committed golden source-to-feature bundle against both the NumPy
oracle and a compiled core:

```sh
python scripts/validate_audio_frontend_bundle.py \
  eval/conformance/audio-frontend/causal-logmel-16k-80-v1.json \
  --native-library .build/libaiii_voice_frontend.dylib
python scripts/validate_audio_frontend_bundle.py \
  eval/conformance/audio-frontend/causal-logmel-48k-to-16k-80-v1.json \
  --native-library .build/libaiii_voice_frontend.dylib
```

The 48 kHz manifest uses the core's owned causal 63-tap windowed-sinc path to
produce 16 kHz processing samples. No platform binding may silently substitute
a system resampler. Both reports say `latency_claims: false`: these are
deterministic numerical conformance results, not live microphone, resource,
device, perceptual-quality, or latency results.

`runtime/audio_frontend/tools/aiii_voice_frontend_runner.c` is the thin native
file runner used for physical executable conformance. It contains no DSP. The
local, SSH, and Android drivers in `scripts/run_audio_frontend_*.py` compile or
invoke that runner, compare its output with both golden bundles, record exact
source/binary/bundle identities, and keep the result at
`numerical_executable_only`. The separate `build_ios_frontend_app.sh`,
`materialize_ios_frontend_cases.py`, and `run_audio_frontend_ios.py` path builds
a signed app around the same C source, verifies the effective strict-C99 build
arguments and copied source bytes, and requires identical results from repeated
fresh processes. None of these paths qualifies microphone or live behavior.

See `spec/PORTABLE_AUDIO_FRONTEND.md` for the frozen manifest grammar,
transform, lifecycle, refusal behavior, and explicit remaining gaps.
