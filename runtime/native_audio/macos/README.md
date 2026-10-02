# M3 native audio host

This is the native-device deliverable for the standalone voice reference. It
provides real microphone capture, speaker playback, Apple voice processing,
explicit device routing, cancellable playback and independently auditable
PCM/timestamps. It is **not yet the model bridge, browser control panel,
near-end admission classifier or human-level qualification**.

The Studio Display path was exercised on this M3. The package targets macOS
14 or newer; that deployment target is not a claim that other OS versions or
devices have been tested. The other four platform families retain their
existing program requirements.

## Build and run

Build the Swift package in this directory. For a stable macOS microphone
identity across builds, sign with an installed local signing identity.

The probe that exercises the host plays a fixed mono 24 kHz reply, cancels,
plays it again in the **same process**, sends a stale cancel, and finishes only
after the second reply drains.

The built bundle is `.build/AII Voice Audio Host.app`. It is a headless helper,
not a new chat UI. Launch through LaunchServices (`open`) as the probe does:
direct execution inherits the launching application's microphone permission
context on this machine. Default signing is ad hoc. The tested local build
used the operator's already-installed Developer ID identity; no notarization,
upload, remote repository write or production deployment was performed.

## Device and audio contract

- Input/output UIDs are explicit. Names in the probe must resolve uniquely;
  there is no silent fallback to the default microphone or speakers.
- Studio Display input and output are separate Core Audio endpoints. An
  ephemeral process-private aggregate establishes the initial AUHAL graph.
  Enabling voice processing replaces AUHAL with VPIO; VPIO is then bound to
  the physical input on element 1 and output on element 0. Both are read back.
- The engine-facing mixer/output format is explicitly matched to the input
  format. On this hardware both streams are 48 kHz Float32. Changing devices
  alone left a stale 44.1 kHz mixer connection during development.
- System defaults are observed before and after, never set. The private
  aggregate is destroyed on the normal and handled-failure paths. A crash or
  missing terminal report is a failed probe, not presumed cleanup.
- Voice processing is explicit; AGC and input mute are disabled. The recorded
  microphone is **processed**, not raw. `--voice-processing off` is an
  experimental comparison mode and has not been qualified by the on-mode runs.
- Separate C single-producer/single-consumer queues carry microphone and
  render callbacks. Each holds at most 64 frames of at most 8192 samples;
  callbacks copy samples/timestamps, and the worker handles files/JSON.
  Overflow is counted and fails the session; it is not silently discarded.
- The actual AVAudioEngine tap delivery in these runs is **100 ms**, despite
  requesting a 480-frame tap. This implementation does not establish the
  120 ms acoustic interruption goal. Smaller callback delivery and the full
  capture-to-admission-to-speaker-stop path must be measured separately.

## JSON-lines model/control boundary

Launch `run` with explicit device UIDs, new evidence directory and
`--stdio on`. Read stdout continuously. `--emit-audio on` adds base64 PCM to
the audio events; capture/reference bytes are also recorded locally. The
model bridge must explicitly convert native rates to each model's rate while
preserving time and continuity. No streaming resampler is implied here.

| Command | Meaning |
| --- | --- |
| `audio` with `synthesis_id`, `pcm_f32le`, `end_sample` | Mono 24 kHz Float32 LE, finite normalized samples, contiguous cumulative end; 1–24000 samples per chunk, at most four seconds outstanding |
| `synthesis_done` with matching ID | Generation ended; playback still must drain |
| `cancel` with ID, or without ID for current playback | Stop/flush active player; stale named cancellations are ignored and recorded |
| `finish` | End input acceptance and drain before closing |
| `stop` | Cancel current playback and close immediately |

Commands have a 140000-byte line bound and a 32-command pending bound.
Unknown/malformed commands fail. The pipe reader uses POSIX short reads: a
small cancel or final chunk cannot wait for 4096 more bytes. A partial line
at EOF is an error. Output distinguishes playback start, confirmed played
chunks, cancellation, drained completion, ignored stale cancellation and
terminal status. A generation token rejects late callbacks from cancelled
playback.

`played` uses `AVAudioPlayerNode.dataPlayedBack`. This is downstream/device
completion notification, **not an independent microphone measurement**.
Cancellation's `completed_samples` counts acknowledged whole chunks; it is
not the exact physical sample at which the speaker became quiet.

The response generator/STT are not in the native cancellation path. The
worker still performs evidence I/O: a stalled consumer/disk can block that
worker, so load/backpressure qualification remains required. No hard-real-time
or universally bounded cancellation claim is made by one fast probe.

## Evidence and verification

Each private session retains source/binary identities, a source archive,
device readbacks, generated-stimulus identity, controller events, native
events, microphone/reference Float32 files, per-chunk hashes, hardware sample
and host clocks, terminal counts, drops and default-device observations.
The probe ignores LaunchServices' exit status as a sole success signal; the
native terminal report and control contract must also pass. A timeout retains
evidence and fails rather than claiming that killing `open` stopped the host.

The independent auditor checks source-archive contents, bound stdout versus
trace, all PCM hashes, complete sample coverage, advancing hardware clocks,
physical device readbacks and the requested playback lifecycle. Tests mutate
routing, samples, timestamps, completion, callback loss, bypass, default
devices, cleanup and short control messages to prove the gate rejects them.

```sh
swift test --package-path runtime/native_audio/macos \
  --scratch-path .build/native-audio-tests --sanitize thread -j 4
```

Actual microphone data and hardware identifiers stay outside Git, in session
directories of mode 0700.

Routing API references used during implementation:
[Apple voice-processing I/O](https://developer.apple.com/documentation/audiotoolbox/kaudiounitsubtype_voiceprocessingio),
[Chromium's explicit VPIO input/output binding](https://chromium.googlesource.com/experimental/chromium/src/+/refs/tags/77.0.3844.0/media/audio/mac/audio_low_latency_input_mac.cc).
The installed macOS SDK headers were also read; these sources support API
usage, not the acoustic performance of this implementation.
