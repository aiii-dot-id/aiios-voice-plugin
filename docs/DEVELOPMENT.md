# Build and validate the voice source

User installation does not require these development tools. Run commands from this repository's
root. Generated files belong under ignored `.build/` or `test-results/`;
models, recordings, credentials and release assets stay outside Git.
`.gitattributes` preserves committed bytes on every OS: automatic CRLF/LF
conversion would invalidate the source inventory and vendored-library hashes.
Keep text edits in their existing line-ending form; do not weaken a hash check
to accommodate an automatically rewritten checkout.

## Model-free native contracts

Requires CMake 3.22+, a C/C++17 compiler and platform build tools. The standalone
session build uses checked-in, hash-verified cJSON and model doubles. It does
not download weights or exercise hardware inference.

```sh
cmake -S runtime/native/session -B .build/native-contracts -DCMAKE_BUILD_TYPE=Release
cmake --build .build/native-contracts --parallel 4
ctest --test-dir .build/native-contracts --output-on-failure
```

Choose an appropriate local build parallelism. The Windows multi-configuration
equivalents add `--config Release` to the build and `-C Release` to CTest.
The real engine additionally needs the verified native dependencies and model
recipes described in [the native build](../runtime/native/README.md). A fixture
worker is never a release worker.

Separator export changes additionally run `tests/test_separator_export.py`
and `tests/test_separator_contractions.py` in the pinned model-export
environment (torch, onnx and onnxruntime), then re-export the exact upstream
checkpoint and run its complete numerical and native cancellation/recovery
panels. Contraction tests cover dynamic time, separate batches, singleton
axes, unknown-equation/rank refusal and generated-name collisions. A mutation
that exchanges the global reduction's batch and feature axes must fail.
These checks are additional to model-free source contracts, not replacements.

The topology fixture uses `-DAII_WORKER_FIXTURE=ON` with the same vendored,
hash-verified cJSON target as the real worker. No external parser path is
required. `tests/test_native_fixture_configuration.py` checks clean configuration
and rejection of modified parser bytes.

## Python tools

Python 3.11+ supports source tests and build tooling. Use an isolated environment:

```sh
python3 -m venv .build/test-venv
.build/test-venv/bin/python -m pip install -r requirements-test.txt
.build/test-venv/bin/python -m pytest -q --fail-on-skips \
  tests/test_native_worker_wire_document.py tests/test_beta3_release_contract.py \
  tests/test_tool_schema_providers.py tests/test_public_privacy.py
```

On Windows, use the environment's `Scripts/python.exe`. Tests requiring
external models or hardware need those explicit inputs. The native plugin
contains no Python interpreter.

## Privacy-clean native builds

Configure each release-owned CMake component, including its nested dependencies,
with `-DCMAKE_PROJECT_INCLUDE=/path/to/repository/runtime/cmake/SourcePrivacy.cmake`.
Use a fresh build directory and explicit dependency source roots. The module
normalizes file macros and debug paths without changing numerical compiler
options. Darwin debug-map object locations become build-relative; MSVC enables
deterministic path mapping and uses embedded object debug information so a
mapped compiler-PDB path cannot become an output location.

Run `python tests/test_native_source_privacy.py` on each target. It compiles and
executes C and C++ targets in both Release and RelWithDebInfo, including an
external dependency and paths containing spaces. The test checks the actual
file macros and executable bytes, not merely successful compiler exit codes.
Before packing and again after signing, run
`python -m scripts.check_native_binary_privacy /path/to/owned/image ...`.
Its category-only report binds each inspected image by hash; it does not replace
manual review of arbitrary personal data, vendor notices or archive metadata.
`stage_qualified_runtime` scans every shipped native image (by header, suffix or
executable bit), not a list of known names: an unrecognized image is scanned and
must be clean. Vendor redistributables that keep their upstream build roots are
declared with `--third-party-images` (schema `aiii.voice.third-party-images.v1`,
exact `sha256` and `distribution` per runtime path, bound by
`--third-party-images-sha256`); their findings are reported, never hidden, and a
release-built name, an explicit private prefix or a credential pattern still
fails. Assembly refuses a stage receipt without this complete census, scans the
assembled package metadata and JSON records, and recomputes Windows Authenticode
from per-image observations: the runtime claim needs every release-built PE
image and the carrier verified, while vendor images are reported as unverified.

`rebuild_native_checkpoint` accepts optional `--uid-frontend`, `--uid`, `--tts`
and `--endpoint` images. They must replace uniquely named existing components.
`--nemo`, repeated, replaces the NeMo diarizer/ASR library as one set: name
every file of the platform's set by its exact file name (macOS
`lib/libnemo_speech_asr.dylib` and `lib/libnemo_speech_asr_c.1.dylib`, Linux
`lib/libnemo_speech_asr.so` and `lib/libnemo_speech_asr_c.so.1`, Windows
`bin/nemo_speech_asr.dll` and `bin/nemo_speech_asr_c.dll`). A partial set, any
other name, a parent that does not declare exactly that set, or a replacement
whose runtime bytes equal the parent's is refused. macOS images are relocated
and ad-hoc signed like every replacement; Linux images must already carry one
`$ORIGIN` RUNPATH. ggml images stay the parent's, so the library must be built
against the same pinned ggml. Every NeMo image is listed in `changed_images`
and `nemo_replaced`; a Windows NeMo DLL is a release-built image that the
signing ceremony signs. The parent stays unchanged, every new image is bound,
and execution/signing/installation claims are reset. A changed binary never
inherits its parent's qualification.

macOS ships Metal kernels compiled at build time. The host's sandbox denies
writes, and Metal keeps its shader cache only when it can write it, so a
library that embeds shader source compiles it on every contained start. `--ggml`, repeated,
replaces NeMo's ggml set whole (`lib/libggml.0.dylib`, `-base`, `-blas`, `-cpu`,
`-metal`); at least one member must change. `--metallib`, repeated, supplies
the kernels of each replaced image built with `GGML_METAL_EMBED_LIBRARY=OFF`:
`default.metallib` (installed as `bin/default.metallib`, where stock ggml looks
beside the executable) for `--ggml`, and `libnative_pocket_resident.metallib`
(installed beside the library) for `--tts`. An image without shader source
and without its kernels, kernels beside an image that embeds source or is not
replaced, and a Pocket library without `metal-library-beside.patch` are
refused.

Build both ggml copies for the Apple-silicon baseline (`GGML_NATIVE=OFF`; a
build tuned for a newer GPU can carry instructions absent on earlier targets), and
compile each metallib from its ggml source directory with the macros every
Apple-silicon GPU uses at runtime, so no build path enters it:

```sh
cd <ggml>/src/ggml-metal
xcrun -sdk macosx metal -O3 -DGGML_METAL_HAS_BF16=1 -I .. -c ggml-metal.metal -o default.air
xcrun -sdk macosx metallib default.air -o default.metallib
```

The tensor API is absent from these kernels; the worker disables it before
loading a model (`worker_environment.h`), or ggml on newer GPUs could tile
its matrix work for kernels the library does not contain. The Pocket build
(`runtime/native_pocket/portable`) pins its Apple options and compiles its own
metallib; its engine source is audio.cpp
`3174e6b26f11a0e39b4f150961dce98f43ba860d` with the five files of
`runtime/native_pocket/engine_overrides` and the two of
`runtime/native_pocket/windows_resident/overrides` in place of the engine's
own, and with `metal-library-beside.patch` applied
(`python -m scripts.prepare_engine_source --layout macos` makes that directory
from an upstream checkout and holds every file to its digest). The worker
holds both ggml copies, whose kernels differ, so the patch makes Pocket's copy
read the library named after its own image instead of a shared
`default.metallib`. `capacity-history.patch` is in this tree and is applied as
a whole in no shipped library: neither of its two changes is in the Windows or
the macOS library, and the Linux library holds the second
(`runtime/native_pocket/engine_overrides_linux/NOTICE`).
## Carrier and package builds

Follow [`plugin/NATIVE_BUILD.md`](../plugin/NATIVE_BUILD.md) for the pinned SDK
and [`maintenance/README.md`](../maintenance/README.md) for the eight carriers.
[`RUNTIME_COMPONENT_SELECTION.md`](RUNTIME_COMPONENT_SELECTION.md) documents
runtime selection and package assembly inputs. Preserve each set's declared
models, runtime hashes, permissions, and resource requirements.

`MANIFEST.sha256` lists source-file digests for source archives without Git
history. Verify it with `python -m scripts.check_source_manifest`.
