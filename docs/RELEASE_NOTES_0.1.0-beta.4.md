# AII Voice 0.1.0-beta.4 — desktop beta

This is an operator-requested **desktop prerelease**, not a completed
human-quality qualification. Current-host installed/browser acceptance and
the actual eight-hour endurance result remain open at publication preparation.
Successful public download/hash readback is a separate publication check.

September 20 source addendum: beta.4 is public. The scheduled eight-hour run
failed and remains unqualified; see [its postmortem](MEETING_EIGHT_HOUR_RESULT_20260919.md).
Overlapping voices can still be merged into one transcript and misattributed by
the pooled-utterance UID path. A speaker-aware reference now passes its first
small recorded panel, but is not part of this signed release. The source update
does not alter the historical release notes already uploaded to GitHub.

## Changes

- Output-only native speech sessions: speak typed replies without opening a
  microphone or creating recognition/VAD/UID inference workers. Requires the
  matching host routing; shared model loading still occurs at activation.
- Process-unique output stream ownership and explicit synthesis introductions
  work with the host's bounded handoff and retired-stream fences.
- Eight worker-declared settings with hearing/speaking scopes, including the
  capture-duration option: default 30 minutes, zero for no automatic stop.
- One package for macOS arm64, Ubuntu x86-64 and Windows x86-64. The host
  downloads only that platform's companion and selected model/data set.
- Speaker recovery parameters now have descriptions, including the exact
  observed hashes needed for confirmed recovery. No enrollment data is
  automatically discarded or reinterpreted.
- Package/catalog host-floor checks, source-only CI, build instructions and
  operator/release documentation are included in the source handoff.
- SDK and AII OS repository publication are owned separately. No SDK checkout
  or system Python is downloaded or required by the installed plugin.

## Requirements and limits

AII OS **0.1.8+**, with the optional-input and mode-routing changes. English
recognition/synthesis and ten voices. No system Python needed. Models/data
total about 3.06 GB per desktop, in addition to the platform runtime, package,
cache and extraction space. See [the operator guide](DESKTOP_BETA.md).

Ubuntu 24.04 must satisfy the host's documented bubblewrap/AppArmor prerequisite.
Do not disable containment. Windows-owned executable images have Authenticode
signatures; the plugin archive has its separate T3 signature. Mac local
signatures are not a Developer ID notarization claim. Backend cancellation
retirement and browser playback stop are separate timings; Windows retirement
may exceed the 250 ms target. UID is probabilistic identification, never an
authentication or permission grant. This is not the Android/iOS release.

## Evidence

The same signed archive is used for all three platform entries:

`id.aiii.voice-0.1.0-beta.4.aiiospkg` — 9,477,409 bytes

SHA-256: `2f74ccfddb247e8085fa711ad3996ad681227c250b7fe4b5057e5b6c026e119f`

Native real-model SDK checks cover output-only speech, interruption/recovery,
return to recorded duplex speech, retained opening words, guided enrollment
and the declared voices/settings. Receipts in those checks are simulated;
they do not certify physical browser playback. Exact artifacts, counts and
unclosed acceptance items are in [the handoff](DESKTOP_BETA4_HANDOFF_20260918.md).
No broader human-quality or speaker false-accept-rate claim is made.
