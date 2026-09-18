# Voice review remediation

Base: `fd6ebb6610a3b73b6dc8612fd69ec82b4feb3002`. Source and deterministic
contract proofs are not installed-product, acoustic-quality or release proof.

## V1 — Graceful drain progress

The 15-second bound is now an inactivity bound during graceful drain, renewed
only by measured recognition, synthesis retirement/generated samples, audio
delivery, or accepted advancing rendering evidence. Admission is already closed;
status calls, duplicate receipts and foreign sessions cannot extend it. Abort
still has its independent five-second bound. Missing terminal receipts fault,
never become successful playback.

Validation: 23 Python worker/control/capture tests and 29 native CTest cases
passed. The new long-playback test fails unchanged on the original worker at
the old 15-second wall deadline and passes on the correction. Duplicate and
foreign-report traffic still terminate a stalled drain. Tests use the production
state machine and explicitly fake models, not physical speakers.

## V2 — Explicit, recoverable UID reset

`speaker.list` now distinguishes typed absence, incompatible documents and corrupt
documents. Storage read errors still refuse: an unreadable store is not empty.
No speakers/eligible captures are fabricated when recovery is needed. The result's
`recovery` object gives the two exact observed hashes (or `absent`). With speech
closed, an AI may propose `speaker.reset` with those two fields nested in
`recovery`; the existing host operator-confirmation gate remains mandatory.

Recovery first writes both original byte sequences into a content-addressed
`uid/recovery-<sha256>.json` archive through the existing host private-file broker,
CAS, durability receipt and readback. Only after that proof does it clear pending
captures and publish an empty profile bound to the current model. Archives cannot
be overwritten with different content; a separately confirmed retry re-attests
the same bytes. A partial reset reports its archive and unresolved state and
requires new inspection/confirmation. Recovery does not convert embeddings or
change identification into authorization. Archives remain private biometric
material; there is no automatic cleanup or public telemetry of their contents.

Native tests cover old-model/corrupt/zero-byte/missing profiles, old captures,
fresh enrollment after recovery, stale confirmation, denied reads, failed and
unsynced archive publication, and each partially failed reset. Carrier tests
hold the exact argument, confirmation, fixed-path and immutable-archive bounds.

## V3 — Reject the ambiguous host version

The candidate now declares a minimum of 0.1.8. All 0.1.7 builds are excluded,
including the ones that accepted the settings but could not stop browser capture
when the engine completed input. This is a minimum requirement, not a claim
that a new host release has already shipped. The host agent has been asked to
assign and qualify the supporting release. Publication/installed-product closure
still requires that release and the finite-limit/status-reconciliation journey.

Operator confirmation: the next AII OS release is **0.1.8**.

## V4 — Unresolved-work bounds and settled-history retirement

The resident worker now passes 1,050 complete session lifecycles and 4,100
fully rendered replies in one activation. The unchanged new tests fail at the
old 1,024-session / 4,096-reply limits on the original worker. At most 64
unresolved generations can own compute/audio/receipt custody. A completed job
is explicitly released only after native retirement, consumed events, transport
END and terminal rendering evidence; another admission cannot evict that debt.
The native high-water generation remains a reuse fence after release.

The worker retains compact SHA-256 identity fences and final receipt counters
for opaque IDs. Those records grow with the activation's ID count: this is
**not a constant-total-memory claim**. They preserve arbitrary old duplicate
receipt idempotency and reject reuse without retaining full job objects, text,
PCM or per-tick history scans. Truly constant identity memory would require a
different host contract (e.g. monotonic caller IDs), not silent forgetting.

Idle polling drops to a bounded 10 ms wait in an open session (100 ms with no
session) when no native work is outstanding; an unfinished input cutoff retains
the active cadence. Native model threads do not signal the worker's condition
variable, so an open session must still bound observation of asynchronous events.
Input, controls and output acknowledgements still wake immediately, and active
inference/recognition retains the existing cadence. In a two-second local fixture
sample on the final candidate, idle CPU was 0.01 seconds after one reply and
0.01 after 4,100 (approximately 0.50% of one core). RSS grew from 2,736 to 3,760 KiB, including identity
fences and allocator effects. This is not a real-model or platform benchmark.
Old receipts, changed receipts, ID reuse across sessions, held inference,
pending-capacity recovery and drain all pass. All 29 native tests pass.

## V5 — One Unicode label contract

The shipped schema now declares the native limit of 128 Unicode scalars (not
512 characters). Seventeen shared vectors cover ASCII, accented characters,
four-byte emoji, combining characters, control bytes, malformed UTF-8 and
empty values. They pass native enrollment preparation and the carrier's schema
assertions. The same vectors also passed an isolated copy of the actual host
schema compiler/validator from `b7f8a6a`; the host checkout was not edited.
The native suite now has 30 passing cases; the carrier package passes plain
and race modes. No enrollment storage limit was widened.

## V6 — The current declaration is a required proof

The required integration gate now runs the actual carrier declaration tests.
The contract is exactly eight host lifecycle controls plus six discoverable
speaker operations. Each speaker tool must declare its summary, input/output
schema, effects, private-file capability and exact confirmation requirement.
The schema files are opened and checked, not merely named. Unhashable operation
IDs refuse with a validation error rather than escaping as a TypeError. The
tests consume the same source-bound carrier directory as the enclosing gate.

Retained baseline probes show that the old checker accepted an incomplete
eight-operation declaration and raised TypeError for an array ID; the corrected
checker refuses both intentionally. Nine additional damaged declarations fail.
Host-only lifecycle controls are not misrepresented as identity-callable tools.

The first full attempt used the system Python 3.9 without pytest-asyncio and
failed (82 failed, 191 passed); that is retained, not counted as a pass. The
gate now rejects a wrong interpreter or missing prerequisites before execution.
`requirements-test.txt` pins the isolated test environment, not the engine runtime.

## Final source evidence and landing boundary

On 2026-09-18, the required source integration gate passed **274 tests with zero
failures, errors or skips** in 136.51 seconds under Python 3.12. The native suite
passed **30 CTest cases**. The Go carrier passed plain and race tests. Carriers
were rebuilt from the bound SDK/source for Darwin arm64 (plain and race), Linux
amd64 and Windows amd64; cross-builds do not establish native platform execution.
The isolated host schema test passed all 17 shared label vectors.

Local evidence: `.build/closeout-r2/{result.json,pytest.xml,pytest.log,idle-history.json}`,
`.build/final-{native-build,ctest,go-plain,go-race}.log`, the earlier per-finding
logs and retained baseline falsifiers. No test exclusion or missing-input skip
was used to produce the passing verdict. Historical artifact-dependent research
tests outside the documented required scope are not counted as newly rerun.

All six findings have source corrections and executable validation. Packaging
must rebuild the worker and native library together: the worker now calls the
new `aii_voice_release_generation` C ABI entry point. Do not pair this worker
with an older runtime. The next package requires host **0.1.8**, including the
engine-completion and subsequent status/UI corrections. The package version is
independent of this minimum host version.

These commits do not install, sign or publish a candidate. A fresh installed
conversation and confirmed UID recovery on the bound candidate remain release
checks; no user's enrollment was changed during validation. The separately
reported cross-session in-flight audio ownership boundary still requires a
shared host/engine protocol decision. Neither that boundary nor acoustic UID
accuracy, mobile hardware execution or human-level quality is claimed closed here.
