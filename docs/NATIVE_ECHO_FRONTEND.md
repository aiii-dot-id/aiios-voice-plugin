# Native reference-aware echo frontend

Status: integrated worker candidate, not enabled in a released plugin.

The operator approved platform-owned echo processing, comparing browser-only,
platform-only and combined processing. Microphone gating during all TTS and
transcript-text deduplication are not substitutes for acoustic cancellation.

## Boundary

One native DSP object per capture generation owns WebRTC AEC3. It consumes
16 kHz mono microphone and mono playback-reference frames, 160 samples each,
on one monotonic sample timeline. One frame of buffering aligns the backend's
128-sample block delay with the unprocessed path; finish returns the held real
tail, never the padding used to drain DSP. A bypass transition must not skip or
repeat the microphone timeline. It runs before VAD, STT and UID. No model,
network access, Python, GPU reservation or background thread is required.
The caller serializes access, just as it serializes input audio today.

Reference means the page's playback mix submitted to its output graph, with
capture/render clock alignment and route generation, not future synthesized
audio. This does not claim hardware audibility. Paused, cancelled, dropped
and never-scheduled output must not appear as rendered reference. The browser
adapter must describe unavailable timing/reference rather than invent silence.

A route reopen starts a strictly newer generation and reconstructs AEC state.
An old generation or out-of-order frame is rejected without advancing state.
Missing reference passes microphone samples unchanged, reports unavailability,
and discards adaptation so later recovery cannot reuse a broken timeline.
Verified reference silence is different from missing reference. Cancellation
remains active for one second after non-silent render to cover acoustic tails;
outside that window microphone PCM passes unchanged. This window is an initial
bounded candidate, not a claim that all rooms have a one-second tail.

The frontend never assigns speakers or removes whole utterances. Unknown AEC
effectiveness remains unknown; the reported processing state is not an accuracy
score. Echo metrics alone do not qualify double-talk or speaker preservation.

## Implementation and dependency

The first backend is the standalone WebRTC AEC3 extraction at
https://github.com/Enaium/webrtc-aec3, pinned to
2cec2f52e26646f93bd2d5498bbabf59cba18da9. Its BSD license and embedded third-party
notices must accompany any distributable. This is an extracted implementation,
not a claim of equivalence to current Chromium's complete audio pipeline.
The extraction includes simplified environment and field-trial adapters; its
x86 runtime CPU-feature probe currently returns false (scalar dispatch).
Keep that distinction in performance reports; a successful build does not
prove parity with upstream WebRTC or AVX2 execution. The integration fixes
missing CoreFoundation linkage on macOS and per-file AVX2/FMA compile flags
on x86 without enabling AVX instructions globally.
Build from an explicit checkout; verify the source binding before compilation.
Do not fetch/build at end-user startup or depend on Homebrew/system libraries.
The signed runtime must contain the compiled libraries and their inventories.

Build this component with CMake from `runtime/native_echo`, supplying
`-DAII_AEC3_SOURCE=<pinned-checkout>`, then run CTest and install to an empty
staging directory. `scripts/prove_native_echo.py` loads that staged library and
accepts external 16 kHz PCM16 microphone/render fixtures. Its report binds
their hashes without embedding paths, waveforms, transcripts or speaker data.
It measures native DSP on constructed mixtures; it cannot establish whether
the original microphone fixture already had browser processing applied.

## Host/browser integration required before enablement

Preserve the existing Off/Listen/Earbud state machine. Publish browser-owned
track/context state, raw echoCancellation setting, page/session generation and
timestamp through the existing host voice surface. Do not change the public
SDK around a private worker process layout.

The host and engine must agree an explicit reference-bearing audio contract:
paired microphone/reference frames on one clock, or an equivalent timestamped
reference plane. Current mono AUD1 input and coarse playback completion receipts
alone are insufficient. Do not silently reinterpret stereo as a reference pair.
The candidate now accepts an explicit `audio.input.channel_roles` array of
`["capture", "playback_reference"]` and confirms it in the open response with
16 kHz, two channels. No declared roles retains the mono path. A build without
the bound echo library refuses the reference layout. The worker never treats
ordinary stereo as a reference. `echo_input.h` accumulates arbitrary transport
fragments into 10 ms pairs and preserves the final real samples. Prepared
output is retained on recognizer backpressure so retry does not re-run DSP.
Finish can precede the last PCM or follow it; a held tail drains in either case.
Each session reconstructs the echo state and reports processing, not acoustic
verification. The host/browser branch must be paired with this candidate.

## Gates

1. Native contracts: generation/clock rejection, invalid PCM, missing versus
   silent reference, reset, no-reference exact passthrough, bounded memory,
   finite output and clean teardown.
2. Real AEC3 controlled delayed/reverberant echo; separately measured near-only
   and double-talk preservation. Keep failures, including startup leakage.
3. Paired real browser capture/playback across normal and rapid Off -> Listen,
   cancellation, delayed output, disconnection and device changes. Compare
   neither (experimental control), browser-only, platform-only and combined
   using the same acquisition/playback conditions. Each condition needs actual
   processing-state evidence; do not rename an already processed recording raw.
4. Complete installed STT/UID/barge-in checks: no TTS-only user turns, retained
   human opening words, no new false identity, stable accepted UUIDs. Then sign
   and install the tested payload. Unit or synthetic tests do not satisfy this.
