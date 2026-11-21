# Build the native voice carrier

Use the Go that the `go` line of `plugin/native/go.mod` names
(`scripts/go_toolchain.py` reads it) and public Plugin SDK 0.1.16-sdk.1.
`sdk-source.json` pins the exact
SDK revision and source archive hash. Obtain that revision with `git archive`,
place the unmodified archive at the declared path, and extract it at the
declared source path. The builder compares every source file, refuses extra
files and local module replacements, and runs offline. Populate its module
cache through a verified Go module proxy first.

The development builder runs on Apple Silicon and produces plain and race
macOS arm64 carriers plus plain Linux and Windows amd64 carriers:

```sh
python -m scripts.build_plugin_carrier --go /absolute/path/to/go
python -m scripts.build_plugin_carrier --verify
```

Outputs go to the pinned `.build` directory or a new `--output` directory.
The build records source digests, toolchain, platform, and binary hashes.
The carrier alone is not a plugin package. Native runtime libraries, model
inventories, settings, and schemas must be bound by package assembly.

## Runtime boundary

Installed activation is zero-argument. The sealed runtime profile selects
one native worker executable beneath its verified runtime root. It cannot
select the carrier itself, escape the root, search PATH, or launch an
interpreter. The carrier forwards host-owned audio handles and does not open
microphones or speakers. `AIISDK_DESCRIBE=1` returns tool declarations without
starting a worker.

Startup, control, storage, model, audio, and retirement limits are documented
in [`NATIVE_WORKER_WIRE.md`](../docs/NATIVE_WORKER_WIRE.md). Worker final events
are flushed before the carrier exits when the bounded transport permits it.
A lost or timed-out admission remains unknown; it is not invented as refusal.

Package assembly requires AII OS 0.1.14 or newer to read runtime extents.
Resource declarations must match each set's actual libraries and models; see
[component selection](../docs/RUNTIME_COMPONENT_SELECTION.md).
