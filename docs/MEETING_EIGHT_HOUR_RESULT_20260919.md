# Beta.4 eight-hour meeting run: failed, unqualified

Recorded 2026-09-19 UTC. This is a terminal postmortem, not a promotion.

## Verdict

The scheduled eight-hour macOS recorded-speech SDK run reached its terminal
coverage checks and failed with `AssertionError(960000)`. That assertion names
the second scheduled one-minute speech interval, not a process crash at minute
one. The harness collected its final events in memory but asserted before
persisting them. Consequently the actual cause of this run's coverage failure
cannot be determined from the retained evidence. It is not an eight-hour pass.

The failed original result remains unchanged at:

`/path/to/work/voice-delivery-20260918/test-results/meeting-eight-hours-r1/result.json`

SHA256: `3d8cfaf458d1243c88e9560f15b02a8680d5ddf903ab445a0d0dff1b36ad8ff0`.

Read-only postmortem and synthetic validator reproduction:

`/path/to/work/voice-meeting-terminal-audit-20260919/audit.py`

`/path/to/work/voice-meeting-terminal-audit-20260919/audit-result.json`

Run the audit with Python 3.11 or newer; it launches no inference.

## What is established, and what is not

| Check | Retained evidence |
| --- | --- |
| Duration | Requested 28,800 seconds. Last of 480 persisted snapshots is at 28,740.011730667204 seconds (7h59m00s). Exact terminal elapsed time was not saved. Intent is not completion evidence. |
| Coverage | Final per-period coverage assertion failed. Final transcript and speaker-observation counts were not saved; neither is zero by implication. |
| Session state | All 480 snapshots report open/accepting, synthesis idle, no session reason, and zero unresolved generations. Snapshot checks also rejected any audio output or failure events observed by then. No complete terminal event/output journal survives. |
| Memory | Carrier RSS 8,080 to 10,832 KiB, peak 11,792 KiB. Worker RSS 4,421,088 to 2,990,656 KiB, peak 4,554,288 KiB. This supports observed process-memory bounds, not completeness of retained event state. |
| Source pacing | Maximum retained source lag 0.16899245930835605 seconds. |
| Byte identity | All 43 run bindings and 324 carrier/SDK source-input bindings match. Packaged Mac carrier equals executed carrier. Fourteen runtime members and 24 declared model files match the bound checkpoint. |
| Retirement | Harness PID 48566, carrier 48581 and worker 48582 are absent. Exit code and whether cleanup escalated were not saved, so absence is not a clean-exit proof. |

The source ordering shows `input_finished` and `session_end` were awaited before
the failing validator, but their full event evidence and final cutoff were not
persisted. This cannot replace a retained completion record.

## Two demonstrated harness defects

1. `scripts/prove_native_meeting_endurance.py:59` requires each final's
   `start_sample` to fall inside its scheduled recording interval. The engine's
   `runtime/native/session/session.cpp` deliberately retains up to 32 blocks of
   512 samples of pre-roll and includes it in that start position. A valid final
   can therefore begin before the scheduled speech while covering its words.
   A pure synthetic probe against the unchanged validator reproduces the same
   `AssertionError(960000)` using a second final spanning 943616..1120000 around
   the scheduled 960000..1120000 recording. This proves a validator blind spot,
   not that this was the only defect in the actual run.
2. Terminal event/count/duration persistence occurs after validation, and the
   failure cleanup does not persist its exit result. A failed acceptance check
   therefore erases the evidence needed to explain it. No actual event stream
   is reconstructed or invented in this postmortem.

## Required next qualification work (not executed by this monitor)

Voice-platform owned:

1. Persist events incrementally and record completion, elapsed time, coverage
   inputs, errors, and cleanup/exit outcomes on every path, before assertions.
2. Make coverage pre-roll-aware using bounded interval association and speech
   content checks. Keep negative proofs for missed periods, stale/repeated
   finals, missing UID observations and a single overlong span masquerading as
   several successful intervals. Merely weakening the assertion is not enough.
3. Pass a short real-engine run covering multiple separated speech periods and
   failure-preservation probes before starting a fresh eight-hour run in a new
   immutable output directory. Retain this failure unchanged.

No production engine changes, rerun, deployment or public publication was
performed for this postmortem. The host has no repair to make on the strength
of this harness failure alone; it must not cite this run as endurance-qualified.

## Provenance and scope

- Qualified production source named by the candidate:
  `a8f3bd03dfbc246747d0cb9554e5c52cc4d09079`.
- Signed beta.4 package archive SHA256:
  `2f74ccfddb247e8085fa711ad3996ad681227c250b7fe4b5057e5b6c026e119f`.
- Mac runtime archive SHA256:
  `106b95724d83c157d3621befbcb507db157b720ae38bcbe41c2ca77edc9e7399`.
- Executed carrier SHA256:
  `4425f1ca233666581273e1753eb254b5cb551e1ea4c6534ae8b7a77b207541fb`.
- Bound harness SHA256:
  `086d4830c9e0c6faa3ea66fff706d50fa0f486347e0493ebcedc34784f422df1`.
- Public source/tag supplied in the delivery record:
  `079cff3ecd68fa94838b3d529e563bc68a1748f0`; later documentation/tooling and
  Windows source/test portability changes do not replace the signed engine.
- Catalog supplied in that record:
  `3c637b81d846ff025f0d6939280963757debb146`.

Beta.4 is already public; this postmortem neither republishes it nor changes
the catalog. It addresses recorded-speech SDK endurance only. It does not
qualify installed/browser journeys, physical audio, broad UID accuracy,
overlapping-speaker attribution, speaker authentication, or human-level voice.
The reported overlap misattribution remains a separate open defect.
