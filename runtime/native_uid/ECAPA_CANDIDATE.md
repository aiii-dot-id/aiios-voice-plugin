# ECAPA native integration candidate

This began as an opt-in development candidate. On 2026-10-01 the operator
promoted it, with its retrained final projection (model `46aef1e6...`; see
`docs/EXPERIMENTAL_UID_HEAD.md`), as the released UID model for 0.1.0-beta.7.
Its build selection remains explicit.
Enable `AII_UID_ECAPA=ON` when building `runtime/native_uid`, with the pinned
PocketFFT root required by `runtime/native_uid_ecapa`. Legacy models remain
available; ncnn builds refuse this option because no ECAPA ncnn conversion
has been qualified. CPU and CUDA are explicit selections, never fallbacks.
The ECAPA frontend is linked statically into the UID library; the standalone
frontend build remains a shared library for independent parity tests. The UID
package therefore does not acquire a separate ECAPA frontend runtime dependency.

## Accelerator qualification

`AII_UID_QUALIFICATION_PROFILE=ON` is a development-only build option. It
requires `AII_UID_PROFILE_PATH`, an explicit ORT profile filename prefix.
It records node placement and timing, not waveforms or embeddings. Do not
package this build. Normal builds ignore this variable and have no new file
output. Requesting a provider alone does not prove hardware execution.

Production Apple UID builds use `AII_UID_COREML=ON` and the explicit
`coreml-ane` request without a profiling directory. The existing
`AII_MOBILE_COREML_CANDIDATE` diagnostic build still requires
`AII_MOBILE_PROFILE_DIR`. Both use identical CoreML provider options.
ECAPA uses Core ML's
`NeuralNetwork` representation: the dynamic `MLProgram` representation crashed
inside the Apple BNNS compiler during repeated fresh-process qualification.
Other components and models retain their existing `MLProgram` selection.
`CPUAndNeuralEngine` permits CPU execution; a CoreML profile is not proof of
ANE silicon placement. Preserve unsupported CPU partitions in reports.

The frozen recorded gate passed on CUDA with the same calibrated decisions,
and the NeuralNetwork candidate passed recorded UUID/session continuity on
macOS. These are component results, not installed release qualification.
Core ML options: https://onnxruntime.ai/docs/execution-providers/CoreML-ExecutionProvider.html

## Whole-engine selection and candidate packaging

The native session build binds `AII_SESSION_UID_BACKEND` independently from
ASR and TTS. Explicit values are `cpu`, `cuda`, `coreml-ane`, and `ncnn-vulkan`.
Omission preserves the previous platform default. Invalid values and conflicting
mobile build selections fail, and the runtime's execution report names this
actual selection instead of borrowing the VAD provider name. The selected UID
library must implement that backend; unavailable providers refuse model loading.
CoreML execution still permits CPU partitions and is not an ANE-only claim.

### Read-only installed runtime cache

On macOS the native host denies filesystem writes. A packaged Core ML build
therefore includes its compiled cache in the signed runtime inventory and
declares its relative directory as `coreml_cache` in `voice-runtime.json`.
The carrier verifies every file before launch, discards any inherited
`AII_VOICE_COREML_CACHE_DIR`, and supplies only that bound directory to UID.
Missing, changed, extra or unbound cache files refuse activation. No host
filesystem permission, SDK interface, CPU fallback or persistent identity
store is added. Cache bytes must be produced from the exact graph/provider
configuration, scanned for private build paths and qualified with recorded
speech under the production wall. A compiled cache passing on one OS build
does not establish compatibility with every supported macOS version.

`scripts/rebuild_native_checkpoint.py` accepts `--uid-model`, `--uid-policy`,
and `--uid-policy-sha256` together with an explicit replacement `--uid` library.
It checks the exact native ECAPA model binding, bounded policy and calibration
digest, replaces the declared UID graph and policy, and preserves the other
model bytes. It removes the incompatible same-model `uid_previous_policy`
selection. Source, model and policy hashes are rechecked before freezing.
The result is unqualified, unsigned, uninstalled and unpublished; prior execution
evidence is not inherited. The builder includes hash-pinned SpeechBrain license,
model card and PocketFFT license/header notices, plus the component attribution,
in its runtime inventory. Missing or changed upstream notice bytes refuse staging.
Release qualification must still verify the platform's complete inference-library
closure. This builder performs no installed-profile migration.

Fresh test installations start with empty stores and acquire new test UUIDs from
speech. They do not require migration of disposable test profiles. Existing live
stores remain untouched by candidate preparation; replacing an installed test
state is a separate explicitly authorized deployment action.

## Model space and compatibility

`configs/uid-ecapa-binding.json` binds the exact ONNX graph and frontend.
Canonicalize it with sorted keys and compact JSON separators before hashing.
The graph accepts `[1, frames, 80]`; its declared output is `[dynamic, 192]`
and every actual result must be exactly `[1, 192]`. Legacy graphs retain their
existing shapes. Signal-quality, cancellation, one-worker and stale-result
publication rules remain in force.

Embeddings now have their actual length. Existing policies imply 256 values;
only the exact ECAPA binding admits 192. Snapshot and pending-recording codecs
check dimensions against that binding. Existing 256-dimensional bytes and
canonical policy fingerprints are unchanged. The private capture ABI retains
its fixed storage capacity of 256 doubles; unused capacity is not part of the
embedding, must be zero, and is never serialized into a 192-dimensional profile.
No Plugin SDK contract or public tool schema changes.

## Retaining UUIDs and labels

`prepare_reembedding` and `prepare_registry_reembedding` are pure offline
preparation functions. The caller must verify original PCM hashes and regenerate
every existing sample with the exact target model. Evidence is matched by its
original audio digest, never by label, a padded vector or a guessed cross-model
similarity. They retain IDs, UUIDs, labels, relationships, creation revisions,
association/link history and enrollment references. Only model policy,
embeddings and the mutation revision change.

Missing, extra, duplicate, wrong-dimension or otherwise invalid evidence causes
refusal. Inputs remain unchanged. Recognition itself never migrates profiles.
These functions do not publish files or grant authority. Production activation
still needs the existing custody/CAS publication owner to bind the complete
target state; do not switch a live model while its enrollment/registry still
names another model. If original recordings are unavailable, retain the old
profiles and model until a separately confirmed enrollment mapping is available.

`prepare_speaker_model_transition` prepares enrollment and registry together
against both expected revisions and a single exact set of regenerated evidence.
An original recording shared by the enrolled and anonymous profiles is supplied
once. Both source hashes are returned for the publication owner. Enrollment UUID
references must keep resolving to the same enrollment (or stay unresolved if
already stale). No files are written and neither model is activated. Publishing
just one document is not a valid model transition.

## Evidence and limits

`model_transition_test.cpp` exercises identity preservation and refusal cases
using synthetic vectors. `ecapa_lifecycle_probe.cpp` exercises the real
`NativeSpeaker` owner, usable-speech admission, session cancel/reopen, registry
matching and fresh-process reload against an explicit private fixture. Neither
probe is included in distribution assets. Recorded acoustic qualification is
separate from those source tests and must bind the exact libraries, graph,
calibrated policy and recordings.

Installed T3/browser testing, overlap attribution, population-level accuracy,
silicon placement, full accelerator accuracy and release packaging remain separate gates.
Passing this candidate does not authorize changing a live identity.
