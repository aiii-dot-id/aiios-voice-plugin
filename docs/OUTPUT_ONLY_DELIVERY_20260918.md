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

Evidence root: `/path/to/work/voice-delivery-20260918/test-results`.
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

## Remaining acceptance

The host's corresponding output-only driver/pump/routing and reviewed ownership
fixes are being implemented by its owner. Its shared open vectors and exact
capability-bearing release must be consumed before the installed browser gate.
The current minimum of 0.1.8 covers the earlier completion capability; it is not
by itself evidence that every 0.1.8 build has output-only routing.

Linux/Windows native execution of this new source, installed browser journeys,
the actual eight-hour meeting run, platform signing, hosted-asset installation
and catalog publication remain separate gates. Carrier cross-builds are not
those gates. No deployment, release upload, broad UID accuracy, physical-audio
quality, mobile or human-level qualification is claimed by this source delivery.
