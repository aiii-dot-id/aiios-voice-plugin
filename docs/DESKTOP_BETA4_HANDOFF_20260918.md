# Desktop beta.4 delivery — 2026-09-18

One signed native T3 integration candidate, three desktop variants. This is
source, engine, packaging and signature delivery, not an installed release
acceptance verdict. At original preparation, no public upload or live-identity
replacement had occurred. Beta.4 was subsequently published on September 18;
see the [current source/release status](SOURCE_UPDATE_20260920.md).

## Exact deliverable

Plugin `id.aiii.voice`, version `0.1.0-beta.4`, minimum AII OS `0.1.8`.
Signed archive `id.aiii.voice-0.1.0-beta.4.aiiospkg`, 9,477,409 bytes:

`2f74ccfddb247e8085fa711ad3996ad681227c250b7fe4b5057e5b6c026e119f`

Local handoff:
`/path/to/work/voice-delivery-20260918/deliverables/output-only-beta4-signed-handoff-r1/`.
It includes twelve release-owned assets, checksums, platform plans, original
upstream evidence, a generated three-platform catalog entry and host verification.
The catalog entry is an input to the catalog owner's signing/publication process;
it is not a published or independently signed catalog index.

The existing authorized platform signer kept its private key on build-host.
Clean host `5b3363f0a5ab41bd6c9d1f0e30b4a29b1fd77f26`, version 0.1.8,
accepts the exact T3 archive and refuses an appended-byte mutant. Every original
manifest/install-root member is unchanged by signature attachment. Windows'
nine owned executable images also have separately verified Authenticode
signatures and timestamps. Mac's local execution signatures are not represented
as Developer ID notarization.

## Native design implemented

The session owns one synthesizer and optional Hearing. Output-only binds a sink
with `audio.input: null`, no microphone handle and no fake input cutoff. It
creates no hearing threads or inference requests. Shared model loading remains
unchanged; this is not a model-memory reduction claim. Stop/cancel/recovery and
receipt-aware drain work without Finish. A retired output-only session can be
followed by a duplex session in the same activation.

Every stream is introduced by synthesis_start naming its session and synthesis;
IDs are process-unique, and no audio follows a stream's END. Independent pipes
can be observed out of order; the host's bounded mapping remains necessary.
The host's shared topology vectors execute on the native parser too.

Meeting mode reuses the existing listening path. Capture limit is an operator
setting, default 30 minutes, zero for no automatic stop. No new meeting engine
or timer guessing the engine's cutoff was added.

## Settings and declarations

Eight settings are packaged from the compiled worker declaration, with their
actual scopes: voice, synthesis language, recognition language, pause,
capture limit, VAD threshold, variation and seed. VAD stays active. Ten named
voices and English recognition/synthesis are supported; no new language is
advertised. Settings take effect at the next session.

| Platform | Startup allowance | Companion download | Companion installed | Model/data download |
| --- | ---: | ---: | ---: | ---: |
| macOS arm64 | 60 seconds | 12,281,714 B | 41,070,463 B | 3,062,640,008 B |
| Ubuntu x86-64 | 120 seconds | 22,360,780 B | 68,425,140 B | 3,062,640,008 B |
| Windows x86-64 | 180 seconds | 112,818,072 B | 364,751,127 B | 3,062,640,008 B |

Allowances are operating limits, not measured startup claims. Prior host-memory
reservations are preserved; unknown device memory is omitted. The companion
sizes exclude the carrier, models, extraction space and caches. Each platform
selects 24 model/data files from the package's 25-file union.

## Evidence and corrections

- Native suites: Mac 31/31, Ubuntu 33/33, Windows 34/34 (platform sets differ).
- Required implementation source scope: 289 passes, no failures/errors/skips,
  plus the carrier package plain/race and the shared topology follow-up.
- All three real native desktop runtimes passed output-only interruption,
  cancellation/recovery and return to recorded duplex speech, retaining opening
  words. Guided enrollment, recorded UID and ten voices/thirteen settings cases
  passed at their documented SDK boundaries. Windows repeats used final signed
  bytes in an ordinary desktop session. Each owned job retired naturally.
- Packaging follow-up: 82 focused tests. Metadata follow-up: 58 tests. Counts
  overlap; they are not additive unique coverage or repeated full-suite claims.
- Actual package assembly refused speaker.reset.recovery's missing description.
  Two regression assertions failed before the correction. The parameter and its
  two exact observed-hash fields are now described; the schema's constraints
  are unchanged. Rebuilding all three carriers proved byte identity, including
  the Windows unsigned parent of the verified signed carrier.
- A prior distribution review had already disposed of the Silero/Smart Turn
  exporter and AsmJit source-build questions. The assembler now accepts an
  explicit digest-bound disposition and checks the exact component and notice
  hashes. It carries the reproducibility limitations forward without inventing
  a renewed licensing blocker or inheriting the previous signature/installation.
- Five catalog probes passed: actual archive and untrusted local-title changes
  generate the same entry; stale version, missing platform and damaged archive
  refuse. The final entry was regenerated from the signed archive.
- Five temporary Windows tasks were removed. Run evidence and the live host
  were preserved. Initial harness/packaging failures remain in their own output
  directories and are not counted as passes.

Evidence roots: this metadata worktree's `test-results`, and
`/path/to/work/voice-delivery-20260918/test-results` plus its
`.build/windows-signed-output-only-r1`. The meeting test ran in the latter
source checkout, deliberately unchanged by the schema correction. Its terminal
failure is recorded in [the September 19 postmortem](MEETING_EIGHT_HOUR_RESULT_20260919.md).

## Remaining gates, explicitly separated

1. The scheduled eight-hour run failed its terminal coverage check. A
   pre-roll-sensitive validator defect was reproduced, but the original harness
   did not persist final events, so that run's actual coverage/cause cannot be
   certified. The failure is preserved; endurance remains unqualified.
2. Joint installed output-only routing waits on the host mode owner. Test the
   exact host artifact that includes routing, not merely any binary printing
   0.1.8. Host 5b3363f0 supplies the optional-input driver/pump; it is not evidence
   that the application always selects that topology.
3. Final installed browser journeys and fresh-download qualification are still
   required on all three desktops: settings/readback, UID capture after session
   close, known/unknown and allow/ignore handling, interruption/opening words,
   recovery, Finish, Abort, reopen, render receipts and retirement. SDK receipts
   were simulated and do not prove a browser rendered audio.
4. Publication is no longer pending: beta.4 is a public prerelease. The source
   update does not replace its signed bytes, re-sign the catalog, or claim a new
   network/acoustic/installed qualification. Historical body-hash evidence and
   the still-open installed gates remain distinct.

No mobile, multilingual, broad speaker false-accept accuracy, physical-audio
quality or human-level qualification is implied by this candidate.
