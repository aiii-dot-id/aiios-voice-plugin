# Desktop engine checkpoint

Status: **recorded-speech SDK gate passed on three desktops; not a finished
public plugin release**. Production source is
`c4595e48c712852e4a084382d23b213606fb37bb`. Later documentation does not change
the qualified runtime bytes. The intended plugin version is `0.1.0-beta.5`.

| Target | Integrated recorded-SDK run | Process retirement |
| --- | --- | --- |
| macOS arm64 | Passed, 124.848 seconds | Exit 0 |
| Ubuntu 24.04 x86-64 | Passed, 161.023 seconds | Exit 0 |
| Windows 11 x86-64 VM | Passed, 277.000 seconds | Exit 0 |

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
- Windows: `0fbfa816db9a2c1f3829794f2f2f95bf928f5ba26cc3f7b0e48e6fc74d478302`

Private retained result-file SHA-256, in the same order:

- `aa3d4d03c84a333f009177f19e9f1545c082a711d0017a5cb16776574f20fabe`
- `ee8ad46e4980d0e25c1a64a673d362c349a50504aa8b1eca768d1901950d351a`
- `67dce4c5f4b6020ad40089baef6f381f8e93404c84668d8dccde3c8d5a37ce75`

The final Windows result follows its endpoint privacy rebuild. The rebuilt
endpoint has bit-identical features and probabilities on all seven paired
recordings. All nine release-owned images on each desktop pass the binary
privacy scan. Failed builds/runs are preserved. Temporary Windows qualification
tasks were removed after terminal success; their evidence was retained.

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
An old host discarding these new fields does not qualify this feature.

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
