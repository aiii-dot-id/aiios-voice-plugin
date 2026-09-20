# Desktop beta.5 preparation

Candidate version: **0.1.0-beta.5**. This is preparation, not a published,
signed or installed-qualified release. The existing beta.4 remains unchanged.

**Release requirement, September 20:** the next release must contain the latest
accepted changes, especially the installed speaker-attribution repair. A
privacy-only rebuild is not an acceptable next release. Publication and catalog
promotion are blocked until the new native hearing path meets the gates below.

## Change and evidence boundary

The new native build configuration removes private source/build paths from
release-owned images, including dependency assertions, Darwin object debug maps
and Windows CodeView references. A successful build alone is insufficient:
the actual compiled C/C++ output is checked in Release and RelWithDebInfo on
all three desktop operating systems. The binary scanner reports categories
and binds inspected bytes without printing the private values it finds.

The checkpoint rebuilder can replace the existing UID frontend, UID wrapper
and resident TTS library explicitly. It retains the original parent and
model inventory, binds changed images and resets qualification claims.

The privacy checkpoint tested so far does **not** integrate the new speaker-aware reference. The
installed recognizer still produces a pooled identity decision for a mixed
utterance; it can attribute another speaker's words to an enrolled person.
Speaker labels are not authorization. A passing single-speaker enrollment
test cannot close the overlapping-speaker defect or qualify speaker filtering.

The endurance run started on this privacy-only checkpoint was cancelled when
the release requirement was clarified. Its partial results and event journal
are preserved as cancelled evidence, not an eight-hour pass. Endurance must run
against the final integrated hearing engine.

## Checks completed during preparation

- Native privacy probe: Release and RelWithDebInfo passed on the local Mac,
  Ubuntu build machine and Windows VM. Earlier failing probes were retained.
- Source scope: 103 Python tests, zero skips; 31 model-free native contracts.
- Mac rebuilt UID/frontend and Metal TTS: changed owned images and carrier pass
  the binary privacy scan after relocation/signing with the local ad-hoc identity.
- Mac recorded-speech SDK tests: durable guided enrollment and restart,
  ten voices across thirteen settings cases, VAD-finalized enrollment,
  known/unknown single-speaker fixtures, spoken interruption, retained opening
  words, recovery and clean process retirement passed.

These are isolated recorded-input tests, not live operator audio, installed
containment, trusted distribution signatures or broad speaker accuracy.

The native speaker-conditioned encoder/decoder now reproduces all 405 reference
tokens across 110 streaming updates in seven recorded conversations. Both an
unconditioned-model mutation and a shared-speaker-cache mutation fail that
comparison. Six neural graph exports pass their declared numerical checks.
See [the native checkpoint and exact remaining boundary](NATIVE_MULTITALKER_RESULT_20260920.md).
These components are not yet selected by the resident worker; the release gates
below remain open and the installed mixed-speaker UID defect is not closed.

## Promotion requirements still open

1. Integrate the measured speaker-aware reference into native hearing. Preserve
   actual foreground/background conditioning and independent track caches;
   compare native output with the pinned reference on the same frozen panel.
2. Bind enrolled-person decisions to suitable speaker-specific evidence, never
   a pooled mixture. Test solo, alternating, simultaneous, unequal-volume and
   cold-overlap speech. Recover both speakers' words; uncertain coverage is
   explicit and is never silently assigned to an enrolled person.
3. Verify the installed consumer path: every final segment has a stable
   session/segment/track reference, sample extent and explicit attribution
   state; late results amend that segment without duplicating a turn. Include
   include/exclude speaker filtering, unknown speakers, track swaps, stale
   observations and enrollment persistence. Speaker identity is not authority.
4. Qualify STT, TTS, UID, VAD, settings, interruption and recovery on the final
   Windows, Ubuntu and macOS runtime images. Old privacy-checkpoint results do
   not qualify a changed hearing model or engine.
5. Verify the Windows publisher signatures and sign the final unified T3
   package; regenerate exact runtime, package and catalog hashes.
6. Complete a fresh eight-hour meeting run with the repaired evidence harness
   against the integrated runtime, including bounded per-track state.
7. Complete isolated installed/browser journeys and fresh-cache acquisition
   from the exact hosted assets on every desktop.
8. Audit the source-to-binary-to-package inventory for all accepted changes;
   verify release metadata, notices and archive privacy. Only after the
   speaker-attribution gate and desktop acceptance pass may this release be
   published and the signed catalog advanced. Reference-only results, a
   version bump or a changed label cannot satisfy this gate.

There is no new public release or catalog promotion implied by this file.
