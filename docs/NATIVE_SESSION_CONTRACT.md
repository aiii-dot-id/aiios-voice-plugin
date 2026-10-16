# Current native resident contract

The carrier and native worker implement this session contract.

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
The host-owned topology vectors are preserved in
`tests/vectors/session_topology.json` with only their narrative description
omitted. Tests bind the public bytes and every request/result field. If the
pinned SDK checkout includes its vectors, their exact payload is also compared. The worker executes all seventeen requests and
confirms both real audio admissions. The complete opens that the host builds (its handles, its mode, the number of the input
stream it declares in `audio.input.stream`, the page's capture report), are
kept in `tests/vectors/host_session_opens.json` with where each comes from,
and the worker is opened with each of them, alone and one after another in
one engine process: frames on the declared stream are heard, frames on
another are dropped and counted. Control-only is expressly a
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

## Input completion under backpressure

Finish admits an immutable exclusive sample cutoff; it is not completion.
The missing-tail budget counts time in which the engine can accept the next
packet. A valid offered packet that the bounded recognition queue refuses
pauses that budget, without increasing queue capacity or declaring samples
received. Recognition progress restores capacity and resumes the remaining
budget even if the caller never retries. Repeated offers, duplicate Finish,
and status calls do not renew the budget. Truly missing audio still faults;
Abort remains independent of the recognition worker. This is not a model
execution deadline or a claim that a slow recognizer is real-time capable.

Recognition's model-call watchdog measures an outstanding synchronous call,
not the aggregate duration of several completed calls. Bounded diarization
refinement reports progress after each replay push and finalization returns.
The separating recognizer forwards that progress through both live and
per-source recognition, and reports completed per-source pushes. No timer,
poll or mere request admission renews the deadline. A callback refusal stops
the remaining work without publishing a final; a call that never returns
still expires at the same configured deadline. These notifications do not
change recognition, attribution thresholds, replay bounds or model inputs.

The semantic endpoint's 250 ms value is a scheduling target, not a fatal
model-execution deadline. At the audio-clock commitment boundary the recognizer
waits for a verdict the time its session states, counted from query
submission: the limits table's `endpoint_decision_ms`, one second by default.
A later verdict is retained as stale evidence; the turn falls back to the
configured acoustic silence maximum without guessing a semantic score or
faulting the session, and the session numbers a `pause_late` event that says
which limit passed. At most one unresolved stale query prevents new semantic
submissions until it retires. Shutdown gives that owner a separate bounded
retirement window, the table's `endpoint_retire_ms`, which a carrier holds
over a model call's time so that the watchdog speaks first; actual model
errors still fault.

## Settings and release inputs

The compiled `OperatorSettings` declaration owns defaults, labels and scopes.
`--describe-settings` requires neither models nor audio endpoints. The packaged
declaration is copied from the built runtime and must agree across platforms;
the assembler does not invent scopes. Hearing settings and the speaking
language are pinned for a session. The speaking voice, its variation and its
seed are taken at the first segment of each reply and never change inside one.

Current profile: twenty TTS presets, English recognition, synthesis in seven
languages (see [speaking languages](SPEAKING_LANGUAGES.md)), adjustable
pause and VAD threshold, sampling temperature/seed, and listening duration.
`capture_limit_minutes` defaults to 30; zero disables duration stopping. It
counts audio including silence, not disconnected wall time. A finite limit
finalizes at its engine-clock boundary and reports why. This is separate from
VAD pause and the bounded guided enrollment recording.

Release assembly requires `--inputs` holding exactly `variants` and
`variant_preference`: a map from each component set's variant ID to its
binding, and every ID once in the release owner's order. All three desktops
must be represented, and a desktop may have several sets. Each binding names
`platform`, `arch`, `stage`, `stage_sha256` (the result.json digest), `carrier`,
`carrier_sha256`, and the complete reviewed `accelerator` object; a GPU set
also names its `reservation_evidence`
(see [component selection](RUNTIME_COMPONENT_SELECTION.md)). No historical
stage is a fallback. Startup allowances and memory reservations are explicit
per-set inputs; measured peaks are separate evidence. Host minimum is
bound to the actual capability-bearing release, not a convenient version label;
assembly refuses one below 0.1.14, the first host that reads the extent every
runtime declares.
Qualify the final host by public release revision and executable digest,
including its optional-input transport and application routing. A version
label alone does not establish compatibility.
