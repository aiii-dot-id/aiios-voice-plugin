# Desktop engine checkpoint

Status: **recorded-speech SDK gate passed on three desktops; not a finished
public plugin release**. Production source is
`c4595e48c712852e4a084382d23b213606fb37bb`. Later documentation does not change
the qualified runtime bytes. The intended plugin version is `0.1.0-beta.5`.

| Target | Integrated recorded-SDK run | Process retirement |
| --- | --- | --- |
| macOS arm64 | Passed, 124.848 seconds | Exit 0 |
| Ubuntu 24.04 x86-64 | Passed, 161.023 seconds | Exit 0 |
| Windows 11 x86-64 VM, publisher-signed bytes | Passed, 357.516 seconds | Exit 0 |

These elapsed times describe the whole test, not per-utterance latency or a
bare-metal Windows prediction. Seven arrangements of two public recordings
exercise solo, alternating, equal/unequal overlap and cold-overlap cases.
The panel is not broad UID accuracy, natural-room separation or human-level
quality qualification.

The actual native carrier/models exercise separated transcripts and speaker
UUIDs, STT/TTS/VAD, interruption, recovery and Finish. A worker restart retains
only the simulated host's private storage; the returning recorded voice reuses
its acoustic UUID. Naming and confirmed forgetting work after capture closes.
Cold overlap yields explicit provisional UUIDs, not a guessed named person.
Host storage and playback receipts are simulated in this gate: this is not an
installed browser/identity test.

## Exact evidence bindings

Runtime manifest SHA-256:

- macOS: `12bbddcf2c860379f1be431c9c9898514e0f969d3d3544e67540b11dfec420d7`
- Ubuntu: `e3c459a8c7ce5403b2ea25416e24ad392d4c2dff2c0dee7a26c08fcf7c19e2a2`
- Windows: `c3a8bbe7a82b6f336d6cabefbb9dea05dfd61b7e1dcfe2205f19f414125b69da`

Private retained result-file SHA-256, in the same order:

- `aa3d4d03c84a333f009177f19e9f1545c082a711d0017a5cb16776574f20fabe`
- `ee8ad46e4980d0e25c1a64a673d362c349a50504aa8b1eca768d1901950d351a`
- `69cc1cad46209f872b8ba0d70abda563019e6a515b79c8c9a63625a96f13df83`

The signed Windows result follows its endpoint privacy rebuild. The rebuilt
endpoint has bit-identical features and probabilities on all seven paired
recordings. All nine release-owned images on each desktop pass the binary
privacy scan. Failed builds/runs are preserved. Temporary Windows qualification
tasks and the two completed signing tasks were removed after terminal success;
their evidence was retained. The signed-byte privacy result is
`1c0cd1f07e1da3e08a6af7d46b432e35b12dc3a64c8c845cdfe446b941467ee6`.
The initial post-signing privacy launcher failed to import its audit module;
that failure is retained. Correcting its module search path did not change any
signed bytes. The corrected audit passes on all nine release-owned images.

The three immutable runtime archives are staged locally, each with the exact
thirty-model download inventory used for qualification. The Windows carrier is
`d4cf68a66643efbf096a8d511ad8f738eba9db7f88422e72175eb36d17363149`.
Its archive is
`226835521a564a4eb924b427b8dbba5873eede5167e04e5b7ff6644da44270df`.
Publisher signing and local staging are not T3 package signing or publication.

The source gate passed 205 publication contracts without skips. Compiled C/C++
privacy probes pass Release and RelWithDebInfo on each desktop, including
dependency headers and the Windows short-path spelling that exposed a bug.
The missing-tail/backpressure fix has a retained falsifier: a full recognition
queue cannot spend the missing-transport-data budget while refusing valid input;
capacity return resumes the remaining budget. Actual missing data still faults.

## Delivery dependencies still open

The host must consume exact-segment UUID/revision/continuity metadata in live
identity input, stored readback and the UI. UUID include/exclude policy must use
that attribution, hold/refuse unresolved allow-list input, and fence stale
updates. Neither an acoustic UUID nor an operator-chosen label is authority.
An old host discarding these new fields does not qualify this feature. The bounded
host consumer landed at `79320fa92b3255e990536ec10105d7481cd9603c`, with the complete
host gate passing: build/vet, uncached exact-once race scopes, cross-build matrix,
static checks, cross-platform vet and packaging. Focused live-steering and
Chrome/Firefox page proofs also pass. This changes the existing speech
interface's consumer, not the Plugin SDK. Installed real-engine/browser and
unsharded release/reference qualification are separate remaining gates.

## General-purpose SDK boundary

Speaker segmentation, acoustic matching and registry storage belong to the
engine. Optional speaker metadata, transcript joins and hearing policy belong
to the host's speech interface. The existing SDK carries opaque declared
operations, events and private-file broker calls; it needs no knowledge of the
models, native library layout, speaker registry or its worker scheduling.
Other plugin types gain no voice-specific requirements. Voice engines that do
not produce these optional fields retain the existing observation contract.
In particular, sample timestamps or a cutoff alone do not claim a speaker track;
only an explicit track declaration opts into the segmented join. The unchanged
SDK voice example remains in the complete host regression gate.

Required next gates remain the consumer's capability-bearing minimum version,
installed/browser acceptance, final publisher/T3 signatures, final-byte meeting
endurance, fresh installation from hosted assets, and catalog replacement.
No new public release or catalog promotion is claimed by this checkpoint.

Current selectable capabilities are ten fixed voices, English recognition and
synthesis, VAD pause/threshold, capture limit (30 minutes by default, zero for no
automatic limit), and synthesis variation/seed. Windows hearing placement is
explicitly CPU while Vulkan synthesis remains enabled; optimal accelerator
placement is not established by this gate. Multilingual speech, mobile targets,
broad UID accuracy and human-level quality remain unqualified.
