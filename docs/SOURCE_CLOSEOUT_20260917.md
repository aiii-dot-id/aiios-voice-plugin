# Voice source integration and validation — 2026-09-17

## Decision and scope

Integrate the reviewed repairs into the authoritative voice source repository,
`build-host:/path/to/work/src/aii-voice-plugin`, without changing the host, SDK, installed
identities, published assets or signed checkpoint packages. This is a source
landing, **not a new release qualification**.

The integration starts at main `4ffad027992f06ba7c081b9b3261bf368777515f` and
includes the nine clean follow-up commits through `6e97fd5`. Their valid
terminal journaling, bounded pipe shutdown, non-object web-frame refusal and
actual notice-digest pins are retained. The changes below reconcile those
repairs with the newer voice working source. The earlier dated reports in
this directory are historical evidence, not claims that their packages were
rebuilt from this landing.

## Reviewed changes and corrections

1. **Interruption selects the current synthesis.** An omitted, empty or null
   synthesis ID on stop/cancel means the current generation. A foreign ID or
   invalid type refuses without fencing the active generation. Playback receipts
   still require their explicit generation; this shorthand does not apply to
   receipts. Idle interruption remains well-defined.
2. **Lifecycle repairs compose instead of replacing each other.** The direct
   awaitable `synthesize(sid, text=None)` API remains compatible, scheduled
   synthesis registers before execution, terminal journaling remains in place,
   and notification failure still releases active-task ownership. A synchronous
   backend cancellation failure is retained while the remaining cleanup runs;
   worker cleanup failures remain failures, not successful retirement.
3. **Diagnostic records stay bounded and serializable.** A private locked deque
   produces a JSON-safe snapshot with an explicit eviction count. The reader
   thread closes its own output pipe when it retires; the owner does not close a
   buffered pipe behind a blocked read. The constructor harness also owns and
   releases resources on allocation, spawn, reader-start and readiness failures.
4. **UID changes are bound to their numerical space.** Exact model size/hash and
   frontend binding select either the existing checkpoint or the measured
   ResNet152-LM replacement. A converted ncnn representation must satisfy its
   own exact graph/weight bindings. No existing enrollment is reinterpreted as
   a different embedding model. Default desktop ORT selection is unchanged;
   ncnn remains an explicit, default-off composition.
5. **Notices and package status are exact.** Release notices use the actual
   pinned digests from the follow-up branch. UID model/notice replacement is
   validated on a copy and committed together only after every check succeeds.
   Windows rebinding preserves verified PE components and distinguishes trust
   verification from test simulations. Integrity, signature, distribution
   review, technical acceptance, installation and publication are separate
   statuses. A boolean qualification field never bypasses inventory checking.
6. **Small native changes stay bounded.** Only two exact unused-initializer
   warnings are suppressed; other warnings, errors and fatal diagnostics remain.
   The optional Linux OpenSSL SHA-256 implementation still hashes every model
   byte and is off by default. It is a candidate, not a newly qualified portable
   dependency or a claimed startup speedup.
7. **The test gate cannot silently lose its proof.** Missing selected modules or
   evidence fail by default. Optional historical browsing requires explicit
   `--allow-missing-evidence`, which is incompatible with the required gate.
   `--fail-on-skips` makes a selected skip fail. Missing test files, an empty
   collection, stale carriers, broken CMake and too-small test counts fail.
   The historical tests were retained rather than deleted to get green.

The source/helpers needed by the selected tests are now present in this
repository. We did not copy all research experiments or cached artifacts.
In particular, the separate experimental MLX graph parameterization and iPhone
compute-plan selector are not part of this desktop source acceptance; no mobile
requalification is implied. The research tree and the other agent's follow-up
worktree remain intact.

## Fresh validation of this source

| Boundary | Result | Evidence |
| --- | --- | --- |
| Required Python/native-dispatch/carrier/package scope | 239 tests; zero failures, errors or skips | `test-results/final-source-gate-r2/` |
| C++ resident-session contracts | 28 passed | `test-results/native-session.xml` |
| C++ ASR contracts and library build | 4 passed | `test-results/native-asr.xml` |
| C++ default ORT UID contracts and library build | 2 passed | `test-results/native-uid.xml` |
| Go carrier whole package | plain and race passed | `test-results/go-plain.log`, `go-race.log` |
| Go static checks | local, Linux amd64 and Windows amd64 vet passed | integration command record |
| Fresh source-bound carriers | Mac arm64 plain/race, Linux amd64, Windows amd64 built and inventory verified | `.build/native-sdk-closeout/build.json` |
| Three repaired development pages | JavaScript syntax checks passed | integration command record; not browser execution |

The 34 native CTest cases are three recorded suites; do not add overlapping
earlier Python runs to the final count of 239. Windows signing tests use
synthetic PE inputs and simulated verification where declared; they are not a
new public-trust signature or a native Windows execution claim.

The unchanged interruption regression was also run against a compiled worker
from `6e97fd5`, linked to the same fixture libraries. Four assertions failed
(empty current-target stop, idle stop/cancel and null-ID receipt); eleven
passed. The corrected worker passes all fifteen. This is an executable
baseline failure, not a compilation failure. Its XML remains at
`test-results/interrupt-baseline.xml`. Earlier failed collection/build attempts
remain evidence and are not counted as passing gates.

Evidence root on the build Mac:
`/path/to/work/voice-integration-20260917-r1/`.
The results and build products are intentionally ignored by Git and retained
outside the source inventory. Selected evidence SHA-256 values:

```text
7618a4140c52e9848a58832464b553a2242adcf9351497a0572fc0eca83e2bd3  test-results/final-source-gate-r2/result.json
3cbea090e25e900fe6cae6f637699eb533ba5b37ce958992bf57f5d119eb1c72  test-results/final-source-gate-r2/pytest.xml
2d9e76b69561565e897bb7a3f0619ef79b289adb6c8cfc4dcda15ebe1662bd46  test-results/native-session.xml
7b0467e1635a5720639a6422702d90855a75810756242af42e97c3d1f8fc6133  test-results/native-asr.xml
163c93040f69b60532e2a17ec5107f1aa63922e759ae169be97353a8a00df076  test-results/native-uid.xml
45ee47530d6f879756cdf766972bd1ce98360a8de04c3865994d2ad36639d23b  test-results/interrupt-baseline.xml
```

## Reproduce the required scope

Use the exact SDK pin and archive verification in `plugin/NATIVE_BUILD.md`,
Go 1.27, a Python environment with the selected tests' dependencies, and a
working CMake/C++ toolchain. SDK pin is unchanged:
`92a42656a039e916140d689342506122185349c5`; archive SHA-256 is
`b56afc82b7172babfd2a749f80f5a1748a07a178d335aae88063411ac195298b`.

```sh
python -m scripts.build_plugin_carrier --output .build/native-sdk-closeout
cmake -S runtime/native/session -B .build/session-contracts \
  -DAII_WORKER_FIXTURE=ON \
  -DAII_WORKER_CJSON_SOURCE="$PWD/runtime/native/vendor/cjson"
cmake --build .build/session-contracts --parallel 4
ctest --test-dir .build/session-contracts --output-on-failure
python -m scripts.validate_source_closeout \
  --carrier-build .build/native-sdk-closeout \
  --native-worker .build/session-contracts/aii_voice_worker_fixture \
  --out test-results/fresh-source-gate
```

Use fresh output directories; do not overwrite prior evidence. Additional ASR
and UID library builds require explicit bound ORT/frontend dependencies as
declared by their CMake files. The required scope is enumerated in
`scripts/validate_source_closeout.py`, not inferred from broad discovery of
artifact-dependent research tests. Two retained real Windows-signing evidence
audits live in `tests/test_windows_signing_evidence.py`; this source-only gate
does not run or certify those external artifacts.

## Landing and release boundaries

`MANIFEST.sha256` now inventories the committed source set, excluding itself.
Git binds the manifest. It is not a package signature. Only source changes are
landed to build-host main; no GitHub push, catalog update, installation, signing,
driver change, live audio capture or hardware requalification is part of this
closeout. Other agents' trees and historical research evidence are preserved.

The signed beta.2 with SHA-256
`4e6c215fb6bec5ec01031c4b0b0229d09104e969834bcf5b4ad4ff3ca1083479`
remains unchanged and **must not be relabelled passing**: its installed browser
barge-in failed. The newer private Mac worker proof is recorded-input evidence,
not a replacement signed, installed or browser-qualified release.

The next product boundary is a new immutable candidate built from landed
source and the agreed host/SDK schema, followed by the installed browser
conversation/UID/VAD/settings/interruption/recovery/receipt/retirement journeys
on each desktop, fresh-cache acquisition, publisher signatures and hosted-byte
readback. Facility revision 3's new profile/schema/compatibility fields are
awaiting the host/SDK agent's landing; they are not invented in this source
commit. Human-level quality and physical mobile acceptance remain separate
requirements. Source green does not discharge them.
