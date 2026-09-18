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

## Python tooling and package checks

Python 3.11+ is a **development** dependency, not part of the shipped native
runtime. Use an isolated environment:

```sh
python3 -m venv .build/test-venv
.build/test-venv/bin/python -m pip install -r requirements-test.txt
.build/test-venv/bin/python -m pytest -q --fail-on-skips \
  tests/test_catalog_preparation.py tests/test_release_status_scope.py \
  tests/test_speaker_input_documentation.py
```

On Windows use the environment's `Scripts/python.exe`. Historical audit tests
may require exact external artifacts named in their contracts. Do not remove
them, silently skip them, or claim `pytest` without those artifacts qualifies
the product. The required integrated source scope is explicitly enumerated in
`scripts/validate_source_closeout.py`; its output records the selected tests,
counts and exclusions from its claim. Model, hardware and installed tests are
additional evidence, not implicit consequences of source tests.

The public `source-contracts` workflow runs the model-free CMake contracts on
Linux, Windows and macOS, and the three package/catalog test files above on
Linux. It has read-only repository permissions and does not acquire models,
sign, publish, install a plugin or certify hardware acceleration. A locally
passing command is not a claim that its first GitHub-hosted run has completed.

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
source commit and the exact tested artifacts; see [PUBLISHING.md](PUBLISHING.md).
