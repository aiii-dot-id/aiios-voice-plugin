# Build and validate the voice source

This is the full native plugin source, not a reduced demo. User installation
does not require these development tools. Run commands from this repository's
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

### Stalled model calls

The native session bounds each synchronous VAD, recognizer, endpoint, synthesis
and speaker-identification call to 30 seconds. This is a fail-stop bound, not a
latency target or an acoustic-quality claim. Each model has its own active-call
deadline; status polling and other models cannot extend it. Idle listening,
input gaps and audio-consumer backpressure are not model calls. Startup and
transport/drain deadlines remain separately owned.

Expiry fences output and faults the session, then requests cancellation. A
model that ignores cancellation remains owned, never falsely retired or freed.
The worker's existing five-second abort-retirement bound ends that process
with nonzero status when necessary. A responsive model can be reused only
after actual retirement; a killed worker must be replaced by its supervisor.
The SDK, wire and operator settings are unchanged.

`native_model_progress_contract` tests all five owners and the idle,
backpressure and recovery boundaries with shorter private test deadlines.
`tests/test_native_model_progress.py` exercises the production 30-second bound
through real dispatcher/ABI/core code with cooperative and stubborn model
doubles, including process exit and recovery. Neither is installed or real-model
qualification. Changed engine bytes still need that qualification before release.

## Python tooling and package checks

Python 3.11+ is a **development** dependency, not part of the shipped native
runtime. Use an isolated environment:

```sh
python3 -m venv .build/test-venv
.build/test-venv/bin/python -m pip install -r requirements-test.txt
.build/test-venv/bin/python -m pytest -q --fail-on-skips \
  tests/test_catalog_preparation.py tests/test_release_status_scope.py \
  tests/test_speaker_input_documentation.py tests/test_speaker_aware_score.py \
  tests/test_speaker_aware_reference.py tests/test_native_meeting_endurance.py \
  tests/test_sdk_host_construction.py tests/test_public_privacy.py \
  tests/test_native_binary_privacy.py tests/test_native_rebuild_libraries.py
```

On Windows use the environment's `Scripts/python.exe`. Historical audit tests
may require exact external artifacts named in their contracts. Do not remove
them, silently skip them, or claim `pytest` without those artifacts qualifies
the product. The required integrated source scope is explicitly enumerated in
`scripts/validate_source_closeout.py`; its output records the selected tests,
counts and exclusions from its claim. Model, hardware and installed tests are
additional evidence, not implicit consequences of source tests.

The public `source-contracts` workflow runs the model-free CMake contracts on
Linux, Windows and macOS, and the package/catalog, hearing and privacy files above on
Linux. It has read-only repository permissions and does not acquire models,
sign, publish, install a plugin or certify hardware acceleration. A locally
passing command is not a claim that its first GitHub-hosted run has completed.

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

## Go carrier

Use Go 1.27.0 and the exact SDK revision/archive hash in
`plugin/sdk-source.json`. Obtain `git archive` from that commit and extract it
to the declared `.build` source path. The builder refuses a mismatched archive,
extra extracted files or a changed module replacement. It does not download
dependencies; populate an isolated module cache from `plugin/native/go.sum`
before an offline build.

The authoring SDK and its clean public mirror do not share commit IDs. At
beta.4 preparation, pin `8155af048f312faec9000defe294b0086b28b62c` was not
present in that mirror. The SDK owner is handling its public source delivery;
this voice release does not publish or modify the SDK. Maintainers can use
the already sealed 1,341,440-byte archive with the hash in `sdk-source.json`.
Do not substitute a floating public main. This developer-source dependency
does not affect installing or running the signed plugin: no SDK checkout is
downloaded by the product.

```sh
python -m scripts.build_plugin_carrier --go /path/to/go
python -m scripts.build_plugin_carrier --verify
```

The four-artifact convenience builder requires Apple Silicon for its native
race binary. Cross-compiled Windows/Linux carriers still require real target
execution. See [NATIVE_BUILD.md](../plugin/NATIVE_BUILD.md) for exact inputs and
proof boundaries. Never substitute the SDK's current main for the sealed pin.

## Changes and evidence

Keep production code, tests, schemas and public contracts in this repository.
Keep dated reports as evidence, not living setup instructions. Do not erase a
failed run or overwrite an output directory to make a result appear clean.
Regenerate `MANIFEST.sha256` from tracked files after staging source edits;
verify it and `git diff --check` before landing. Publication must use a clean
source commit and the exact tested artifacts.
Run `python -m scripts.check_public_privacy --history` before a public push;
an integrity inventory does
not prove that its contents are appropriate for publication.

## Component-family packaging

`tests/test_beta3_release_contract.py`, `tests/test_release_model_bindings.py`
and `tests/test_authoring_sdk_binding.py` exercise distinct Full/Small bindings
on all desktops, exact selection order, per-set model/resource preservation,
carrier substitution refusals and separately sealed authoring SDK inputs.
`tests/test_qualified_runtime_stage.py` covers explicit component-set IDs while
retaining the historical single-checkpoint default. These are package/source
contracts, not voice-model or installed qualification.

Use the explicit input and stage contracts in `RUNTIME_COMPONENT_SELECTION.md`.
Historical platform-keyed assembly inputs must be rebuilt from their retained
qualified checkpoints; the publisher does not guess which old directory is a
new Full or Small set. Do not alter previous receipts to satisfy a new contract.
