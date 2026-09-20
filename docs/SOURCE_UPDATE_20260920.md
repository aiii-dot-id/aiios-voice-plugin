# September 20 voice source update — prepared for GitHub

## Deliverable and release boundary

This update brings the voice repository forward from public main
`079cff3ecd68fa94838b3d529e563bc68a1748f0`, including the speaker-aware reference
milestone `6de17063707c0bf4f23fc1233905806cf61d1824` and the endurance proof repairs
described below. It is a source preparation, not a new signed plugin release.

Public prerelease **0.1.0-beta.4** remains the installable desktop package, with
minimum host **0.1.8**. Its archive is unchanged:

`id.aiii.voice-0.1.0-beta.4.aiiospkg`

SHA256 `2f74ccfddb247e8085fa711ad3996ad681227c250b7fe4b5057e5b6c026e119f`

Anonymous GitHub API readback on September 20 confirmed public main `079cff3`,
the September 18 published prerelease and this asset digest/size (9,477,409 B).
This was metadata readback, not a new complete asset-download qualification.
The existing [source-contract run at that public commit](https://github.com/aiii-dot-id/aiios-voice-plugin/actions/runs/35390448010)
passed on Windows, Ubuntu and macOS. It is not a CI result for this unpushed update.

No native carrier, worker, production runtime or sealed SDK pin changes are in
this update relative to `079cff3`. Do not bump the package version, replace the
beta.4 tag/assets or alter the signed catalog for source-only reference work.
A future changed native payload needs a new package version and its own signing,
hosted-byte and installed-product checks. SDK and AII OS publication remain with
their owners.

## Included work

1. **Speaker-aware hearing reference.** Pinned Sortformer v2.1 and Multitalker
   Parakeet produce separate anonymous speaker streams on seven frozen cases
   built from two human recordings, including overlapping voices. Both runs
   passed the first engineering gate; MeetEval independently agreed with the
   error counts. The model pins, runner, scorer, falsifiers and measured result
   are included. See [the result and native integration boundary](SPEAKER_AWARE_REFERENCE_RESULT_20260920.md).
   This is not enrollment matching, a native implementation, a five-platform
   port or a human-level accuracy claim.
2. **Endurance evidence repair.** Events are journaled incrementally on the
   actual SDK reader. Terminal observations and cleanup outcomes survive failed
   acceptance checks; preflight refusals now leave a result too. The coverage
   validator permits bounded pre-roll and finalization context while requiring
   speech content in every scheduled interval and exactly one UID observation
   per final. Lost periods, wrong text, duplicate observations, stale spans and
   one overlong span cannot masquerade as successful coverage.
3. **Honest release documentation.** The eight-hour run is failed/unqualified,
   not still running. Its original evidence and [postmortem](MEETING_EIGHT_HOUR_RESULT_20260919.md)
   are preserved. The README, operator guide, publishing guide and source notes
   distinguish the public beta from this newer reference implementation.
4. **Source CI.** The declared model-free Python scope now includes the
   speaker-aware scorer/runner, endurance validator/journal and SDK owner
   construction tests. Native source-contract jobs remain on all three desktop
   runners; their unchanged production scope is not represented as newly run
   hardware qualification.

## Validation performed for this update

The seven Python files enumerated in `docs/DEVELOPMENT.md` and the workflow
passed **77 tests, zero skips**. They run without private evidence, models or
an SDK checkout. The signed runtime was not changed to obtain those passes.

A fresh test-only checkpoint rebuilt the macOS carrier against the current
sealed source. It reproduced the beta.4 carrier byte for byte:

`4425f1ca233666581273e1753eb254b5cb551e1ea4c6534ae8b7a77b207541fb`

All native worker, library and model bytes were reused only after verification.
The old checkpoint was left intact. An initial attempt with its older source
inventory correctly refused before model launch; that refusal was retained.

The repaired harness then ran **90.554 seconds** of paced, recorded synthetic
speech through the real macOS five-model runtime and SDK lane:

| Check | Result |
| --- | --- |
| Speech coverage | Three separated intervals, three finals, 0 word edits / 42 reference words |
| Speaker observations | Three, each linked to its final; all correctly report enrollment unavailable, not a named-person accuracy result |
| Meeting behavior | Capture limit zero, no synthesis events, no output audio frames |
| Completion | Exact 1,440,000-sample input cutoff, terminal session event |
| Retirement | Exit 0, no reader/cleanup errors; carrier and worker independently absent |
| Evidence | 42 events retained; all 47 input bindings rechecked after execution |
| Endurance | `full_eight_hour_run: false`; this short test cannot replace the failed eight-hour run |

Local immutable evidence is in
`/path/to/work/voice-github-prep-20260920/test-results/`:

- `source-tests-r3.xml`: the explicit 77-test scope.
- `meeting-journal-preflight-refusal-r1/result.json`: refused old source binding.
- `meeting-journal-smoke-r2/result.json`: SHA256
  `5c7d010b4852a23f5d4c81f9e83e1a2665f8fb5861d1a6880aa76bb5ce8871d0`.
- `meeting-journal-smoke-r2/events.jsonl`: SHA256
  `78fa1d514dbabb87abee6564d84838151d142af355361a5c48bc6985b03c3d4d`.
- `meeting-journal-smoke-r2/audit.json`: independent post-run binding, event and
  process-retirement checks.

The test used a simulated host and prerecorded input. It did not touch Test Identity A,
Test Identity B, microphones, enrollment documents or installed plugins. No public push,
upload, catalog change or live deployment is part of this preparation.

## What remains before the next native release

The published beta.4 still pools mixed-speaker utterances and can attribute
another speaker's words to an enrolled person. Its UID must not grant authority
or be presented as secure overlap filtering. Source-level reference success does
not close that defect.

The next native delivery must preserve the reference model's speaker-conditioned
state, recover independent per-speaker text, associate enrollment only with
suitable speaker-specific evidence, and prove the host join/filtering behavior.
Then repeat installed interruption/recovery and platform-specific acceleration
checks. A fresh completed eight-hour run with the repaired harness is still
required for an endurance claim. Keep working TTS, settings and cancellation
contracts intact while integrating the new hearing path.
