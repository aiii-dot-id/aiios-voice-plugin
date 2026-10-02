# AII Voice desktop beta

## What the package provides

One signed `id.aiii.voice` plugin selects the companion for macOS arm64,
Ubuntu x86-64 or Windows x86-64. Models are data downloaded separately. The
browser owns microphone and playback; AII OS owns the session, containment,
downloads, integrity checking and permissions. The engine runs locally, not
in a cloud speech service. Initial acquisition requires internet access.

The desktop beta contains English STT and TTS, ten selectable voices, active
VAD and conversational pause handling, interruption/recovery, guided speaker
enrollment and UID. Meeting input and output-only speech are native session
capabilities; their use in the UI depends on matching host routing. A package's
signed minimum host version must name the first *published* host that carries
the host features it needs. A development build's version number alone is not
evidence that a public installer contains those features.

This is a desktop beta, not a claim of universally human-level quality,
multilingual support, biometric authentication or a mobile release.

## Install

1. Update to the compatible AII OS release named by the catalog and signed
   package. Install AII Voice from its Plugins catalog. A staged package or
   unpublished release URL is not an installation source.
2. Review the download, installed-space, memory and permission declarations.
   The package downloads only the selected platform's runtime and model set.
   Do not download all desktop companions or install a system Python.
3. Allow acquisition and integrity verification to finish. Do not override a
   refused hash, missing prerequisite or containment refusal. Record the
   exact error and package/host version when reporting a failure.
4. Open Speech settings, select AII Voice and the desired voice and pause.
   Start a new speech session and check the engine's effective settings.

The unified package is about 9.6 MB. Platform companions are approximately
12.4 MB (macOS), 26.8 MB (Ubuntu) or 112.9 MB (Windows), before extraction;
selected models/data are roughly 3.1 GB per platform. Extraction, download
cache, installed runtime and update rollback need additional space. These
figures are not RAM or GPU-memory requirements. The signed release declares
exact byte counts and reservations; no unmeasured GPU-memory peak is promised.

Ubuntu 24.04 may require the host's documented bubblewrap AppArmor profile.
Use the AII OS-supported prerequisite/installer path; do not disable AppArmor
or replace graphics drivers to make a plugin start. Windows uses the ordinary
interactive desktop launch context, not a service-session performance proxy.
macOS targets Apple Silicon, not Intel. The candidate's Windows-owned images
are Authenticode signed; local Mac execution signatures are not Developer ID
notarization. T3 package verification is a separate check on every platform.

## Operator settings

These settings are emitted by the compiled worker, packaged unchanged and
read back from the opened session. Save changes, then start a new session;
there is no promise of applying them mid-utterance.

| Setting | Meaning |
| --- | --- |
| TTS voice | Select one of the ten declared reference voices. All segments reuse it. |
| Speaking language | English in this release. No unsupported locale is advertised. |
| Recognition language | English in this release. |
| Turn pause | Silence allowance before committing a conversational turn. |
| Capture limit, minutes | Default 30; **0 means no automatic stop**. Counts accepted audio, including silence. |
| VAD threshold | Speech-detection sensitivity; VAD remains active. |
| TTS variation | Sampling temperature within the declared bounds. |
| TTS seed | Whole-number sampling seed; this does not promise cross-backend bit identity. |

The capture limit and the turn pause are different: a pause ends an utterance;
the capture limit ends input for that session. At a finite limit, the host
should stop the microphone, show the engine's reason, finish the last accepted
words and drain the reply. It is not an abort. Zero does not remove queue
bounds, cancellation, receipt requirements or fault handling.

## Speaker identification

Explicit guided capture creates a pending enrollment sample. Speak once for
enough usable speech, stop, review the result and confirm the chosen stable UID
and label. Pending explicitly requested evidence survives session close and
restart; you do not need to keep speaking while an AI calls enrollment.
Poor or insufficient evidence must produce a reason, not an invented match.

AI-callable speaker operations include packaged summaries, examples and input/
output schemas. Use discovery/list results for IDs and capture handles; do
not guess them. List, removal, reset and confirmation do not require a live
microphone. Mutations retain operator confirmation and host-owned private
storage. Enrollment is not ambient audio collection.

The newer native engine carries separate speaker-track references through
recognition and attribution. A first uncertain or insufficient utterance keeps
its final/track reference and a pending reason; it does **not** receive a
fabricated permanent speaker UUID. A new anonymous UUID needs corroborating
clean evidence from a distinct utterance. An operator can later attach a label
or, with confirmation, link two UUIDs known independently to be the same
person. Labels and UUIDs are continuity claims, never authentication.

The host's only/ignore UID policy controls which recognized text reaches the
identity. Restricted delivery withholds unresolved finals and unclassified
partials. A compatible host keeps a bounded, explicit recent-heard buffer so
withheld speech can be searched as untrusted context without replaying it as
a command. This buffer is process-local, expires after thirty minutes, and is
not an archive of raw audio. Verify the actual host release and policy readback;
exposing a speaker score or ID alone is not proof of filtering.

Overlapping voices, short utterances, changed microphones and noise remain
open accuracy risks. Separate track references and conservative pending
decisions prevent a mixed utterance from being silently asserted as a trusted
speaker, but do not establish word-perfect separation or broad speaker
accuracy. Do not use UID as the sole authorization for sensitive actions.

## Update, rollback and report

Use the host's plugin update and rollback facilities; do not replace a running
worker or private enrollment file manually. Preserve profiles and operator
settings. If an enrollment model/policy conflict is reported, use the explicit
confirmed recovery workflow rather than deleting or reinterpreting the file.

For a useful beta report, include host version, plugin version, OS/architecture,
selected voice and effective settings, whether speakers or headphones were
used, and the timing/error evidence. Do not attach enrollment data, raw voice,
transcripts or credentials without deliberate consent. A session that faults
or omits words is a bug to investigate, not a normal beta limitation.

See the [delivery contract](BETA1_VOICE_RELEASE.md) and the current release's
notes for the exact signed hash and passed gates. Native model/SDK checks with
simulated render receipts do not establish that a browser or physical speaker
played every sample. A recorded-speech installed pass is not a human listening
assessment; report dropouts, false speaker joins and missing words explicitly.
