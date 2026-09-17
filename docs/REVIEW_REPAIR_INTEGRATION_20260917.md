# Review repair integration — 2026-09-17

## Scope and source identity

Reviewed `dev8:/work/aiii/src/aii-voice-plugin`, branch
`fix/review-20260916`, HEAD `abe2e88043a94e71757f06485635feabebae68dd`,
against snapshot base `153ec31bfe746a0b2e40f692b08d77d513e4b5a9`.
The source handoff is `docs/REVIEW_FIXES_20260916.md` in that repository.

Integrated selected changes into `/Volumes/AIII Models/work/voice-frontier`.
All fourteen affected pre-existing runtime/carrier/page files matched the
snapshot base byte-for-byte before integration. Existing unrelated modified
and untracked work remains intact. The remote branch and remote main were not
changed. This is working-source integration, not a Git merge commit, release,
deployment, or qualification of a new signed package. The working source was
already largely untracked; no unrelated snapshot was committed as part of this
review.

## Disposition

| Incoming commit | Disposition |
| --- | --- |
| 087bdcc | Integrated: reserved input-completion queue slot and regression. |
| 484205b | Integrated with corrections: synchronous synthesis registration, retained direct awaitable `synthesize(sid, text=None)` API, notification-failure cleanup. |
| d65da7c | Integrated: trace validator models buffered playback after synthesis cancellation without accepting unknown synthesis IDs. |
| d145d5a | Integrated with correction: synchronous backend cancellation failures also participate in cleanup error collection; actual worker-process fault/retirement test added. |
| 5c32cba | Integrated: input continuity belongs to a stream, not to a `(kind, stream)` pair. |
| dca588e | Integrated with correction: private bounded deque, locked JSON-safe record snapshot, explicit dropped-event count propagated into all four evaluation reports; pipes closed. |
| e5980ac | Integrated: second readiness is a fault after the first has been consumed. |
| d01b5fe | Not integrated: automatic exclusion of missing modules/evidence and skips for broken CMake do not belong in the authoritative release gate. Neither its collection gate nor the SDK-vector skip was imported. |
| 3a5d442 | Integrated: malformed JSON socket frames are caught in three development pages. This does not claim full message-schema validation. |
| 9cba600 | Integrated: constructor stop publication under lock, refusal before PulseAudio consumption, ASR frame extent check, named worker capacity failure, bridge sender retirement. Native proof boundaries are below. |
| 4ffad02 | Not integrated: optional digest parameter with no supplied pins changes no normal release-notice verification. Actual pins and mismatch tests remain required. |
| abe2e88 | Read as the source handoff; replaced locally by this integration-specific record. |

## Defects reproduced and corrections made during integration

Before runtime changes, five incoming Python regressions failed with the named
harm: finish refusal after cutoff, escaped synthesis cleanup error, overlapping
input kinds accepted on one stream, valid cancelled-playback trace refused,
and child pipes left open. The new Go second-readiness regression also failed
against the original carrier and passed after the change.

The incoming `LiveSession.synthesize` signature broke the working repository's
existing 100-reply lifecycle test with a TypeError. Its awaitable API is now
retained while the actual scheduled path registers synchronously. The first
notification write is inside cleanup ownership, so a failed write releases the
registered active synthesis.

The incoming public deque was incompatible with four JSON-producing evaluation
scripts. `EvidenceClient.record()` now returns a locked, JSON-safe snapshot with
`events_dropped`; `messages` remains a JSON-safe list view. A 5,001-event process
test retains exactly 4,096 records, reports 905 evictions, and saves valid JSON.

A new regression reproduced an additional shutdown escape when the backend's
synchronous cancellation hook raises. Cleanup now retains that error and still
retires tasks, output and speaker resources. A real Go-carrier/Python-worker
test injects invalid backend audio, observes the failure, confirms the cleanup
diagnostic, verifies child retirement and refuses any invented `session_end`.

## Evidence and limits

- Evidence directory: `deliverables/review-repairs-20260917-r1/`.
- The incoming diff is retained there as `incoming-runtime.patch`; it includes
  the unadopted SDK-vector skip for faithful review history, not as the applied
  integration patch.
- Python results: `pytest.xml` and `result.json` hold the final selected suite
  result: 219 tests plus 12 subtests passed in 56.78 seconds, zero skips or
  failures. One pytest assertion-rewrite warning is retained. This is the
  relevant integration suite, not the entire research tree.
- Native Mac configure/build and all 27 CTest contracts pass in
  `.build/review-repairs-20260917-r1/`; CTest retains its test log there.
- Go carrier full package tests pass plain and race; local vet plus Linux and
  Windows cross-vet pass. Four fresh carrier targets were built and inventory
  verified under `.build/native-sdk-review-repairs-20260917-r1/`, using the
  unchanged SDK pin `92a42656a039e916140d689342506122185349c5`.
- The first broader Python run refused three stale default-carrier bindings.
  No integrity check was disabled. The wire tests now accept the same explicit
  verified `AII_TEST_CARRIER_BUILD` selection as the existing process test.
  The old default build and all signed release artifacts remain untouched.
- Worker and ASR translation units pass C++17 syntax checks with their actual
  headers; the three edited pages pass JavaScript syntax checks. Existing native
  UI, playback routing, RTC and capture-worklet tests pass.
- Ubuntu 24.04 dev7 compiled and ran `pulse_refusal_test.c` with real libpulse
  16.1. It calls the production read routine with a real mainloop lock but opens
  no server connection, microphone or speaker. The original implementation
  compiles and fails its unchanged test on the consumed-queue assertion; the
  repaired implementation passes both refusal and healthy-read assertions.
  Remote evidence is `/var/tmp/voice-review-pulse-20260917-mO7wXH/`.
- No driver, device routing, identity, credential, package, catalog or public
  repository was changed. No test-exclusion gate was added.

## Still required before release promotion

Follow-on 2026-09-17: fresh Mac native builds pass 3 ASR plus 28 session
contracts, real acoustic references, five-model guided capture/restart and
speech/UID/interruption/recovery, and thirteen settings cases. Evidence:
`deliverables/review-repairs-native-macos-20260917-r1/README.md`.
That build used the older CPU-reference link recipe, not the staged Metal
release recipe; its thirteen PCM hashes differ from the Metal parent. It is
additional functional source evidence, **not** accelerated release parity.
The existing signed package remains byte-identical. Work is paused at the
operator's request after those runs retired.

Rebuild the full native worker/runtime/carrier packages, then re-run exact-byte
model, interruption/recovery, UID/VAD, settings, installed browser and retirement
gates on each target desktop before signing/promoting those new artifacts.
The native thread-creation/lost-wakeup case is inspected and covered by general
session contracts, not a deterministic injected thread-creation failure proof.
The ASR short-frame and worker-capacity edits have compilation/inspection
evidence, not injected runtime-failure proofs. The PulseAudio refusal test is
not a physical capture-hole journey. No human-level qualification follows from
this source repair gate.

The handoff's diagnostic-host constructor ownership issue was subsequently
resolved in voice working source at 11:46Z. Readiness is indeed a separate
method; the actual defect was allocation/spawn/reader-start cleanup. Five
executable baseline failures now pass, and the relevant 29-test suite includes
real carrier/worker lifecycle regressions. This is a diagnostic harness repair,
not a public SDK change or an engine rebuild. See
`deliverables/sdk-host-custody-20260917-r1/README.md`. Outdated overlay bindings
remain a distinct, per-qualification source-pin check, not a reason to loosen
the acceptance.

## Resumed execution — accelerated desktop parity, 2026-09-17

The operator resumed execution after the pause above. Fresh repaired builds now
pass the native and complete recorded SDK gates using the accelerated release
parents on all three desktops; the earlier CPU-reference build is not substituted.

| Platform | Evidence | Independent readback |
| --- | --- | --- |
| macOS / Metal | `deliverables/repaired-metal-beta1-20260917-r1/run/result.json` | `deliverables/repaired-metal-beta1-20260917-r1/repair-audit.json` |
| Ubuntu 24.04 / Vulkan, shared dev7 | `dev7:/work/aiii/repaired-vulkan-beta1-20260917-r2/run/result.json` | `deliverables/repaired-vulkan-beta1-20260917-r2/repair-audit.json` |
| Windows 11 VM / existing DirectML and Vulkan profile | `deliverables/repaired-windows-beta1-20260917-r4-evidence.zip` | `deliverables/repaired-windows-beta1-20260917-r4-audit.json` |

All three pass guided capture/closed-microphone enrollment after restart,
five-model conversation/UID/VAD/interruption/recovery, and thirteen settings
cases. All thirteen TTS PCM hashes match each platform's own accelerated parent.
No cross-platform bit identity is claimed. Voice processes retire normally;
the Windows build's separately owned compiler telemetry helper was explicitly
retired and recorded, not confused with a clean inference-process shutdown.

Windows's first failed attempts are retained. They exposed an MSVC telemetry
process surviving the compiler and a test executable finding an unrelated
System32 ORT before the intended PATH entry. Build helpers now have exact
image-bound custody; tests carry the bound DLLs adjacent to their executables.
No System32 file, driver, installed application, or OS policy was changed.
All four temporary repair tasks were removed after evidence collection.

The runtime verifier now accepts a well-typed `qualified` boolean without
weakening inventory/hash checks. Publication output separates integrity,
signature, distribution review, technical acceptance, publication and installed
status; one stale boolean is no longer used to describe all those boundaries.
The focused tooling gate passed 53 tests with no exclusions in
`deliverables/repaired-release-tooling-20260917-r4.xml`. A preceding misspelled
test-path invocation ran zero tests and is retained, not counted as a pass.

These repairs are rebuilt and tested, not yet a replacement signed public
package or installed browser qualification. Original signed artifacts and live
identities remain untouched. `UID_MODEL_REPLACEMENT_20260917.md` records the
separate commercial-friendly model candidate; its results must not be attributed
to these repaired-baseline artifacts.
