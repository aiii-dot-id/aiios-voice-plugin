# Beta.5 signed package checkpoint

**Superseded candidate, not publishable:** the subsequent installed Windows
check found a filename-based ONNX loading failure inside AppContainer. The
standalone signed SDK pass did not cover this boundary. The
[bound-model repair](WINDOWS_BOUND_HEARING_MODELS.md) must pass installed
qualification, and the unified archive and catalog must be re-signed against
its changed Windows bytes. The archive hash below is retained historical
evidence, not the next release's final hash.

The unified `id.aiii.voice` version `0.1.0-beta.5` archive selects one of three
native variants: macOS arm64, Windows x86-64 or Ubuntu x86-64. Each selects
thirty hash-bound model files. No system Python is required. The Plugin SDK
remains unchanged; speech-specific behavior belongs to the engine and the
host's optional speech consumer.

Signed archive SHA-256:
`ed88bc265c5d6d14d1aa66c1a7763292dc681d3e0c3d2041f601aae7f7bbf36a`.
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

All upstream model bodies were downloaded anonymously and hash-checked again.
Nineteen release assets and the signed-package catalog input are staged.
This is not a hosted-download or installed-conversation success claim.

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
