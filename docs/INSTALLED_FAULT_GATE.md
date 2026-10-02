# Installed worker fault and recovery gate

`tests/host_native_fault_test.go` is an opt-in external test for the host's
`internal/pluginhost` package. It changes no host production code or SDK API.
Copy it into an isolated host checkout, compile that package's test binary,
and run `TestVoiceInstalledFaultRecovery` from the package directory (the
host's TestMain builds its worker relative to that directory).

Required explicit inputs:

- `AII_FAULT_PACKAGE` and `AII_FAULT_PACKAGE_SHA256`: bound parent package.
- `AII_FAULT_CANDIDATE` and `AII_FAULT_FREEZE_SHA256`: rebuilt checkpoint and
  its exact integrity record.
- `AII_FAULT_MODELS`: root containing the declared model files.

The fixture verifies those bytes, reconstructs the runtime archive, replaces
the selected platform's carrier, and signs the test package with a temporary
test root. Production installation verifies runtime/model hashes. Asset fetches
read the bound local files; this is **not a public-download or release-signature
proof**. Temporary signing keys, models, recordings and private paths must not
be committed with the fixture.

The test requires real contained five-model activation and a complete TTS
reply. It then terminates a worker during synthesis, observes fault, verifies
reap, audio-endpoint release and runtime unpinning, reactivates and requires a
complete new reply. It repeats with a stopped worker and a pending status call.
No successor audio may enter the retired sink. Signal targets must be the
uniquely identified worker child of that test activation; no live identity is
used. The fixture never resumes a stale PID later during cleanup.

The application normally registers `PinReleased()` for a live session. The
fixture does likewise: an error is not an engine terminal event and cannot
release custody before verified process retirement.

First passing Mac run: voice source `df496f2`, host source `0dbff0db`, Go 1.27.0.
Five models under Seatbelt; 51.04 seconds total. Worker termination was observed
in 32.76 ms; whole-worker stall in 7.046 seconds. Baseline and both recovery
replies each delivered 115200 samples at 48 kHz. This uses captured PCM and
simulated terminal playback receipts, not a browser, speaker, microphone or UID
accuracy test. It is not the host's canonical release gate or Windows/Linux
installed qualification.

The separately tested model-call watchdog detects a stalled inference call
while the worker's control lane remains responsive. Stopping the whole worker
here instead tests its carrier/supervisor boundary; neither substitutes for the
other.

## Recorded-input integration

`TestVoiceInstalledCaptureContinuity` adds `AII_FAULT_RECORDING` and its exact
`AII_FAULT_RECORDING_SHA256`. The approved recording must be canonical 16-bit,
16 kHz mono WAV, between two seconds and one minute. The test repeats it with
four seconds of separating silence, opens an installed meeting session, and
requires multiple speech starts/finals, exactly one speaker observation per
final, the exact input cutoff, no synthesis, a successful drain and endpoint
release. It never prints the transcript or speaker data.

The continuity candidate passed: 912624 input samples, two speech starts, two
finals, two exact speaker-observation joins, zero synthesis; 44.42 seconds wall
time. The same candidate also passed the worker fault/recovery gate in 51.94
seconds. These checks exercise real models through the contained installed
host/carrier, but do not score transcription against human reference words or
prove speaker identity correctness. Repeating a saved clip is not fresh
physical-microphone evidence or independent enrollment corroboration.

## Actual speaker identity assertion

`TestVoiceInstalledSpeakerIdentity` runs the same contained recorded path but
requires every final's speaker observation to be `known` with the explicitly
expected enrolled ID. Additional inputs are `AII_FAULT_EXPECTED_SPEAKER`,
`AII_FAULT_ENROLLMENT`, `AII_FAULT_REGISTRY`, and the two documents' corresponding
`_SHA256` variables. The fixture copies those documents into a fresh test
identity; it never points the test host at a live identity's private-data root.
Do not commit these inputs, labels or biometric data.

The joint-gallery repair at `f12ab4c` passed on Mac with two finals and two
correct identity observations, 908784 input samples, exact joins, no synthesis,
drain and endpoint release, in 46.83 seconds. The predecessor candidate failed
the same assertion on the same input and copied profiles with
`cross_gallery_ambiguous` in 27.21 seconds. This is a recorded installed-path
regression proof for the corrected decision, not broad speaker accuracy,
microphone-condition/overlap qualification, a public signature or a deployment
to the operator's identity. Host source was `0dbff0db`, Go 1.27.0; host production
code and SDK were unchanged.
