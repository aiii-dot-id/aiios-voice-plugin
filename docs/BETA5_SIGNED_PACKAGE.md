# Beta.5 signed package checkpoint

**Signed candidate; release acceptance remains in progress.** The Windows
[bound-model repair](WINDOWS_BOUND_HEARING_MODELS.md) has now passed the
installed AppContainer journey, and the unified package and catalog preparation
are re-signed against its exact bytes. Publication, anonymous acquisition and
installed browser acceptance remain separate gates.

The unified `id.aiii.voice` version `0.1.0-beta.5` archive selects one of three
native variants: macOS arm64, Windows x86-64 or Ubuntu x86-64. Each selects
thirty hash-bound model files. No system Python is required. The Plugin SDK
remains unchanged; speech-specific behavior belongs to the engine and the
host's optional speech consumer.

Signed archive SHA-256:
`2b852d0d00a3a97ff8136688fe71544508cfa373ebc9e58532c0b685437eea70`.
Host `79320fa92b3255e990536ec10105d7481cd9603c` verified its T3 signature and
rejected an appended-byte tamper. The archive's original members are identical
before and after signing. Windows publisher signing covers the shipped images.

The package contains seventeen declared operations, including nine speaker
operations with complete input/output schemas, parameter descriptions and
examples. Anonymous speaker buckets can be inspected and named after capture
closes. The eight settings cover voice, recognition/synthesis language, pause,
VAD threshold, capture duration, temperature and seed. Current selectable
languages are English; ten voices are included. UUIDs and names are model
attribution, not authentication or authority.

## Installed recorded-speech evidence

The same signed archive passed production-host materialization, T3 verification,
contained activation and the recorded-speech journey on all three desktops:

| Target | Activation | Complete journey | Accelerator readback |
| --- | ---: | ---: | --- |
| macOS arm64 | 13.17 s | 16.84 s | CPU hearing / Metal TTS |
| Ubuntu 24.04 x86-64 | 14.67 s | 21.56 s | CPU hearing / Vulkan TTS |
| Windows 11 VM x86-64 | 131.64 s | 164.72 s | CPU hearing / Vulkan TTS |

These are individual observations, not latency guarantees or normalized
cross-platform benchmarks. The Windows run used unchanged AppContainer
filesystem and network restrictions. Its full signed SDK speech/UUID panel
also passed in 237.05 seconds; all child processes retired.

Each installed journey exercised closed-session speaker discovery, output-only
TTS with 48 kHz playback accounting and terminal receipt, meeting STT with the
exact final/track/UUID join and no synthesis, session close, post-close bucket
readback, and deactivation with zero retained runtime references. The inputs
were recordings and verified preseeded dependencies. This does not prove a
fresh network installation, physical microphone/speaker quality or browser
interaction. The eight-hour meeting run is still pending, not passed.

All upstream model bodies were downloaded anonymously and hash-checked again.
Nineteen release assets and the signed-package catalog input are staged. The
earlier archive `ed88bc265c5d6d14d1aa66c1a7763292dc681d3e0c3d2041f601aae7f7bbf36a`
failed Windows installed startup; its failure is preserved and it must not be
published as the final candidate.

## Redistribution

The new hearing exports retain the complete NVIDIA Open Model License, its
required attribution, pinned model cards and an inventory of export changes.
The original license permits commercial use and redistribution subject to its
conditions; it is not replaced by the application's Apache-2.0 license.
See [NVIDIA's model terms](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/)
and incorporated [Trustworthy AI terms](https://www.nvidia.com/en-us/agreements/trustworthy-ai/terms/).
Use must respect applicable biometric consent and other legal requirements.
The packaging review is bound to the exact model and notice hashes and cannot
clear a different model or any unrelated open item.

The model cards' limits remain: English-focused recognition, at most four
diarization speakers, and possible degradation with noise or long recordings.
Our recorded desktop acceptance panel has two speakers. It is not broad
room-acoustics accuracy, mobile qualification, or a human-level quality claim.
