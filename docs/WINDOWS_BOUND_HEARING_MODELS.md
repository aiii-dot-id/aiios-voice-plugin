# Windows contained hearing-model loading

The installed beta.5 qualification exposed a Windows-only startup failure that
the standalone SDK test did not exercise. ONNX Runtime's filename-based loader
canonicalized the external-weight directory. The native plugin wall grants
access to declared model files, not enumeration of their parent directories.
The repair must not expand those grants or disable containment.

The Windows hearing adapter now uses the existing read-only model mapper. It
opens the six declared graphs and five encoder shards by explicit filename,
checks their exact sizes and SHA-256 digests on the same opened files, and passes
the verified bytes to ONNX Runtime. The external-initializer memory API copies
weights during session construction. Mappings remain alive until construction
and unchanged-file checks complete. Only then are they released. No model file
is modified, and the graphs cannot choose additional filesystem paths.

The native profile's paths are UTF-8, including non-ASCII user directories.
The adapter converts them explicitly to native paths. Unknown graph names,
missing files, wrong sizes and altered bytes fail before model admission.
The model bindings intentionally fail closed when an export changes: a new
export requires a corresponding binding and release qualification.

Only the Windows loading path changes. Unix loading, model contents, feature
extraction, diarization, independent speaker caches, decoding, session controls
and the generic Plugin SDK do not change.

## Required evidence

- Build and run `aii_multitalker_bound_session_test` against the release's exact
  ONNX Runtime library. Its default mode checks invalid bindings using a
  non-ASCII temporary directory. With a model-root argument it also checks a
  missing/truncated shard and opens all six real graphs.
- Run the full recorded-speech SDK panel on the rebuilt worker, then again on
  the publisher-signed runtime and its rebuilt/signed carrier.
- Repeat the installed signed-package journey under the unchanged AppContainer.
  A standalone graph or SDK pass cannot substitute for this check.
- Rebind the unified package and catalog to the final signed Windows bytes;
  retain the failed predecessor evidence, never relabel it as qualified.

On Windows, placing the exact runtime DLL beside a standalone diagnostic is
necessary: adding its directory to `PATH` alone can still select an older
system-installed ONNX Runtime earlier in DLL search order. The shipped worker
already uses the side-by-side runtime layout.
