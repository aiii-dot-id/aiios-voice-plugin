# Current native resident contract

This is the source contract. A source test is not an installed or release
qualification. Historical checkpoint reports retain their own artifact bounds.
Authority: James's 2026-09-18 execution instruction and the host's exchange
confirmation `20260918-1812-output-only-contract-confirmed.md`.

## One engine, optional input

AII OS owns meeting/interactive/earbuds/off policy, browser audio endpoints,
identity turns and UID delivery filtering. The engine does not auto-answer a
transcript. Explicit synthesis works in both duplex and output-only sessions.

Duplex open retains both handles and both formats. Output-only uses the same
`speech.session.open` with an output handle, `audio.format: s16le`, explicit
`audio.input: null`, an output format, and NO `input_handle` key. Null or empty
input handles are not substitutes for absence. Omitted input format is invalid.
The accepted result explicitly returns `audio.input: null` and the actual
24 kHz mono output format. Duplex confirms its actual 16 kHz mono input too.
The host-owned `spec/audio/vectors/session_topology.json` is copied byte for
byte to `tests/vectors/session_topology.json`. The worker executes its twelve
requests and confirms both real audio admissions. Control-only is expressly a
kit proof-engine mode; the native speech engine refuses it (no audio binding).

Output-only status reports `input.state: absent`,
`recognition.state: inactive`, and `input_completion: null`. It emits no input
completion or transcript, admits no input PCM, and refuses `finish_input`.
Unexpected PCM faults the lane rather than being recognized. The hearing model
accessors and hearing inference workers are never entered for that session.
Models remain loaded by the existing activation; this is not lazy unloading.

`close(drain)` on output-only waits for compute retirement, transport END and
terminal playback receipts, without a fictional microphone cutoff. Stop,
cancel, receipts and abort are unchanged. A denied microphone cannot prevent
typed replies. No microphone means no acoustic barge-in; explicit stop and a
host-owned transition to duplex remain available. Topology is fixed at open.

The private C ABI has one canonical `aii_voice_open_session` options entry.
Existing open entrypoints delegate with input enabled and retain their layouts
and defaults. Worker and runtime library must be rebuilt together. One optional
`Hearing` group composes STT/VAD/endpoint/UID with the required synthesizer.
No dummy hearing model and no synthetic `finish_input(0)` is used. Live-final
enrollment discovery in an output-only session is empty, never a predecessor's
retained evidence; durable guided-capture enrollment remains independent.

## Output ownership and receipts

Every output stream has one `synthesis_start` with explicit session ID,
synthesis ID and output stream ID. IDs are process-unique and exhaustion is
refused, never wrapped. There is no unnamed echo or greeting path. No frame
follows a stream's END. The terminal synthesis notification follows the actual
END write. Two independent pipes can be observed out of order: the host must
route by explicit ownership, not whichever session is current at read time.

Generation, transport delivery and rendering are separate counters. Only a
valid host playback receipt proves the rendering claim it actually carries.
Cancellation does not manufacture a full-tail receipt. Every unresolved job
retains custody until compute, output and receipt obligations have retired.
At most 64 unresolved generations are admitted; settled jobs are reclaimed.
Compact replay/identity fences grow with activation ID count. There is no
constant-total-memory or infinite-lifetime claim.

## Settings and release inputs

The compiled `OperatorSettings` declaration owns defaults, labels and scopes.
`--describe-settings` requires neither models nor audio endpoints. The packaged
declaration is copied from the built runtime and must agree across platforms;
the assembler does not invent scopes. Settings are pinned for a session.

Current profile: ten TTS presets, English recognition and synthesis, adjustable
pause and VAD threshold, sampling temperature/seed, and listening duration.
`capture_limit_minutes` defaults to 30; zero disables duration stopping. It
counts audio including silence, not disconnected wall time. A finite limit
finalizes at its engine-clock boundary and reports why. This is separate from
VAD pause and the bounded guided enrollment recording.

Release assembly requires `--inputs` containing exactly macos/linux/windows.
Each row names `stage`, `stage_sha256` (the result.json digest), `carrier`,
`carrier_sha256`, and the complete reviewed `accelerator` object. No historical
stage is a fallback. Startup allowances and memory reservations are explicit
per-platform inputs; measured peaks are separate evidence. Host minimum is
bound to the actual capability-bearing release, not a convenient version label.
The host's 20260918-1843 exchange records James's release ruling: no 0.1.8
release has gone out; this is 0.1.8. Its optional-input transport is on host
`5b3363f0`; application routing follows the mode-owner landing. Qualify the
final host by commit and executable digest. Older staged 0.1.8 builds do not
become compatible merely because they print that version.

## Executable checks

- `native_output_only_contract`: C ABI without accessible hearing models;
  exclusive lease, cancellation, recovery, real receipt debt and idle drain.
- `tests/test_native_output_only.py`: dispatcher topology refusals, absent-input
  status, no invented finals, unexpected PCM refusal, successive sessions and
  process-unique named streams with END discipline.
- `scripts/prove_checkpoint_current_interrupt.py --output-only`: bound real
  SDK/native checkpoint, three stop/cancel/recovery cycles and producer checks;
  synthetic receipts, not browser rendering. `--recorded-conversation` then
  reopens duplex on the same process for the recorded STT/interruption journey.
- `test_native_settings_packaging` and `test_beta3_release_contract`: compiled
  scopes survive assembly unchanged; absent inputs/resources refuse.

Installed browser tests, platform signatures, actual hosted asset downloads and
desktop/endurance qualification remain independently measured release gates.
