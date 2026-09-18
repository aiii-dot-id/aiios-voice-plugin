# Output-only source delivery — 2026-09-18

Base: `39b51e389acb407ede9eafa6610c5b7988673874`. SDK pin:
`8155af048f312faec9000defe294b0086b28b62c`. The agreed wire is documented in
NATIVE_SESSION_CONTRACT.md. No host/SDK source or live identity was edited.

## Delivered

One synthesizer with optional Hearing (recognizer, VAD, endpoint, UID), composed
at open. Output-only never constructs hearing inference threads or calls those
model accessors. It refuses input and Finish, reports absent input, and drains
actual output and rendering debt. It can retire and reopen duplex on the same
activation. Shared model loading remains unchanged.

The compiled settings declaration now supplies scope. Release assembly requires
explicit hash-bound desktop inputs and platform-specific resource declarations.
It neither silently uses an old stage nor invents settings scopes/startup values.
No settings, model, supported language or voice-quality claim was expanded.

## Executed evidence

Evidence root: `/Volumes/AIII Models/work/voice-delivery-20260918/test-results`.
These files are not runtime dependencies or public release assets.

| Boundary | Result | Evidence |
| --- | --- | --- |
| Original worker and new output-only probes | Four intended failures, four existing refusals pass | output-only-baseline.xml |
| Native core/C API/UID/settings/transport suite | 31/31 pass | native-r2.xml |
| Required source integration scope | 289 pass, zero failures/errors/skips | source-closeout-r1/result.json and pytest.xml |
| Go carrier package | Plain and race pass | Executed with Go 1.27.0, same SDK/source as bound carrier |
| Real Mac SDK/native models | Three output-only stop/cancel/recovery cycles; duplex recorded speech afterward | real-macos-output-only-r2/result.json |

The real-model journey retained “cobalt lantern seventeen” in its transcript.
Eight streams were explicitly introduced with session/synthesis identity, had
unique process IDs and ended without post-END audio. Playback reports were
simulated by the test sink, not measured browser rendering. Ready time in this
run was 3.37 seconds with existing OS caches; this is not a cold-install claim.

Bound Mac runtime manifest:
`d22ddc45e0bcde2cc49998032d1fa19b3e0b74309101cc4158f0e318f2a6a372`.
Bound carrier:
`4425f1ca233666581273e1753eb254b5cb551e1ea4c6534ae8b7a77b207541fb`.
The worker and session library changed together. Unchanged parent model and
library bytes were verified; parent execution qualification was not inherited.

The first real-model attempt completed the output-only cases, then stopped on
a missing test-only SciPy resampler. It remains recorded as r1 (failed), not a
complete pass. The full rerun used an isolated Python 3.12 environment with
requirements-test.txt plus scipy==1.15.3. No Python dependency was added to the
native product. The first fixture attempt also retained a teardown error;
the corrected harness waits for the expected faulted-worker exit.

## Desktop execution and shared contract follow-up

The same production change now builds and executes natively on Mac M3,
Ubuntu 24.04/dev7 and the Windows 11 VM/GTX 1070. Native tests passed 31/31,
33/33 and 34/34 respectively (platform/real-backend test sets differ).

All three passed the three output-only interruption/recovery cycles and the
subsequent recorded duplex journey, retaining the opening words. Mac additionally
passed the Javert and Marius variants of the output-only proof. These are SDK
and simulated-sink results, not acoustic judgments about the reported dropouts.

Mac and Ubuntu passed durable guided capture/restart/enroll plus ten voices and
thirteen settings cases. Ubuntu also passed live-final known/unknown UID and
held-storage interruption. Windows' ordinary desktop-session UID/settings gates
are tracked separately; its service-session output-only result is not promoted
into desktop containment or installed qualification.

Windows initial attempts retained failures for test-only SciPy and packaging
dependencies. The successful complete rerun is `output-only-real-r4`; the test
requirements now declare both dependencies. Native product dependencies did not
change. Qualification evidence remains under the platform-specific task roots:

- Mac: this checkout's `test-results/` and `.build/checkpoint-macos-r2`.
- Ubuntu: dev7 `/work/aiii/voice-output-only-20260918/`.
- Windows: `C:\work\aiii-voice\voice-output-only-20260918\`.

Host `5b3363f0` and SDK `3f4f225` landed the optional-input binding/pump/driver
and shared examples. Their twelve open requests now execute against our native
worker too. The three vector files are byte-identical, SHA-256
`98105f7418ab1d02a7fef0f02d7920e56c2fe93e40ddda1ef46293a779f51605`.
The focused follow-up scope passes 35 tests, no skip; unchanged production code
was not rebuilt for these test/declaration-comment additions. The carrier still
pins SDK `8155af0`: the successor adds topology helpers/reference-engine work,
not changes to the transport used by this carrier.

Unsigned, uninstalled companion archives are inventory-verified:

| Variant | Compressed bytes | Installed companion bytes | Archive SHA-256 |
| --- | ---: | ---: | --- |
| macOS arm64 | 12,281,714 | 41,070,463 | `106b95724d83c157d3621befbcb507db157b720ae38bcbe41c2ca77edc9e7399` |
| Linux x86-64 | 22,360,780 | 68,425,140 | `dcf003d6d89b0c93721aad80d2c47161a1773d4737f2b09d9aac0799813d0337` |

These counts exclude carrier and models. They are not total installation sizes.
Staging receipts are `.build/stage-{macos,linux}-output-only-r1/result.json`.
Windows immutable archive construction requires signatures over the replacement
images first; the prior images' signatures are not inherited.

## Remaining acceptance

The host's 20260918-1843 handoff records the operator ruling that 0.1.8 has not
been released. Thus 0.1.8 is the agreed minimum; qualify the exact host artifact
at/after `5b3363f0`, not an older staged binary sharing that number. Typed/earbuds
application routing remains the mode owner's work before the installed gate.

The eight-hour wall-clock meeting run is active, not passed. It feeds periodic
recorded speech plus real-time silence with capture limit zero, requires each
scheduled recording to yield a final and UID observation, and forbids synthesis.
The twenty-second harness smoke is explicitly not endurance evidence.

Installed browser journeys, final-byte signing/qualification, hosted-asset
installation and catalog publication remain separate gates. No deployment,
release upload, broad UID accuracy, physical-audio quality, mobile or human-level
qualification is claimed by this source delivery.
