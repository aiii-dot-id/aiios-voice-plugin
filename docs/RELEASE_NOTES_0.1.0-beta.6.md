# AII Voice 0.1.0-beta.6 — desktop prerelease draft

**Publication hold:** This draft is not a release announcement. The signed
package must be rebuilt against the first *published* AII OS host version that
contains the bounded heard-history and speaker-policy consumer repair. Public
0.1.8 does not. The current private beta.6 package has an obsolete 0.1.8
minimum and must not be cataloged. Complete installed/browser acceptance and
physical listening review on the replacement bytes before publishing.

## Scope

One signed T3 plugin selects a native companion for macOS arm64, Ubuntu
x86-64, or Windows x86-64. It downloads only the selected platform's
hash-bound runtime and model data. No system Python or cloud speech service
is required. English recognition and synthesis, ten selectable TTS voices,
VAD and adjustable turn pause, conversation barge-in/recovery, output-only
speech, meeting mode, and guided speaker enrollment remain available.

This candidate strengthens speaker continuity: unresolved speech stays
unresolved; a new anonymous UUID needs clean corroboration from a distinct
utterance. Track and final references remain separate from identity labels,
and the operator may later name or explicitly link UUIDs. An ambiguous match
is not authentication or command authority. Overlap separation is still a
model estimate, not a guarantee of word-perfect attribution.

The candidate also reserves initial synthesized audio before playback to
reduce underflow during generation pauses. Machine-paced timing checks saw no
new scheduling gaps in the tested path. A physical listening pass and
browser-render accounting are separate release checks; this statement is
not a claim that all audible breakup is cured.

## Required host and operator controls

Install only on the compatible AII OS release named by the final signed
manifest and catalog. The host's approved-only and ignore-UUID policies
decide which speech becomes a conversation turn. Unresolved or disallowed
speech may be retrieved only by explicit bounded, untrusted heard-history
readback; history must not replay it as a command or wake a turn. This is a
host behavior, not a privilege conferred by the plugin's UUID estimate.

The browser owns microphone and playback. The microphone's Off, Listen and
Earbud states, actual session retirement, playback receipts, and SAFE
containment must be verified on the host build used for release. Revisit
Speech settings after installation and start a new session for changed
settings to take effect. The capture limit defaults to thirty minutes;
zero disables that automatic limit, not fault, resource or queue bounds.

## Evidence and limits

The private candidate has passed source tests, signature/tamper checks and
contained recorded-speech installation on macOS, Ubuntu 24.04 and Windows
11 VM. A live test identity has used the signed private candidate, and a
model-facing heard-history read returned a nonempty, explicitly untrusted
row. Those observations do not establish a fresh anonymous public download,
full browser/physical-audio quality, broad speaker-ID accuracy, arbitrary
multi-speaker separation, or mobile qualification. The final replacement
package and exact host release must be rechecked independently.

The models' upstream licenses and notices are shipped with the runtime and
data inventory; the application's Apache-2.0 license does not replace those
terms. Obtain consent appropriate to voice and biometric processing. Do not
use speaker attribution alone to authorize sensitive actions.

For the final signed archive hash, exact host minimum, asset hashes and
completed gate results, use the publication receipt accompanying the release,
not an earlier private build or this preparatory draft.
