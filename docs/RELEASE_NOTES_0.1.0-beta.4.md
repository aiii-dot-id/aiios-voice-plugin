# AII Voice 0.1.0-beta.4 — prepared release notes

Publication status: **prepared, not published**. The final installed/browser
gate, endurance result and public download readback must be recorded before
calling this release qualified. This file is the release-note draft, not an
announcement that the catalog has changed.

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
- The exact sealed Plugin SDK source archive is a developer release asset,
  because its authoring commit is not in the clean public SDK mirror. It is
  not downloaded or installed by the plugin and does not change its binaries.

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
