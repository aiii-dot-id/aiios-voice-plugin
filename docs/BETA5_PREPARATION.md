# Desktop beta.5 preparation

Candidate version: **0.1.0-beta.5**. This is preparation, not a published,
signed or installed-qualified release. The existing beta.4 remains unchanged.

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

This does **not** integrate the new speaker-aware research reference. The
installed recognizer still produces a pooled identity decision for a mixed
utterance; it can attribute another speaker's words to an enrolled person.
Speaker labels are not authorization. A passing single-speaker enrollment
test cannot close the overlapping-speaker defect or qualify secure filtering.

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

## Promotion requirements still open

1. Finish and requalify the rebuilt Windows and Ubuntu runtime images.
2. Verify the Windows publisher signatures and sign the final unified T3
   package; regenerate exact runtime, package and catalog hashes.
3. Complete a fresh eight-hour meeting run with the repaired evidence harness.
4. Complete isolated installed/browser journeys and fresh-cache acquisition
   from the exact hosted assets on every desktop.
5. Resolve the overlapping-speaker acceptance failure before describing this
   candidate as a speaker-attribution repair. Do not represent source-only
   reference results as installed capability.
6. Verify release metadata, notices and archive privacy; publish the prerelease
   only on its demonstrated scope, then update the signed catalog after the
   hosted-byte and installed acquisition checks pass.

There is no new public release or catalog promotion implied by this file.
