# UID replacement: measured candidate, not silent enrollment migration

## Decision and exact artifact

Carry WeSpeaker VoxCeleb ResNet152-LM forward as the commercial-friendly UID
candidate. Do not train a new recognizer from zero to solve a redistribution
problem that an existing, measured recognizer already addresses.

- Repository: `Wespeaker/wespeaker-voxceleb-resnet152-LM`.
- Revision: `4adba1525a6c9d5fff74b6df43a6ec97a86c4112`.
- ONNX: 79,158,228 bytes; SHA-256
  `5b734353b4b410e222bbd124dd095537642237ad895727d18a3b9fee330262a8`.
- Native frontend/model binding:
  `2575d15495d0e1cf17a2a12b0163dec0639db6218b3fe810e70369002d3898da`.
- The model card declares Apache-2.0. The upstream WeSpeaker pretrained-model
  policy declares CC-BY-4.0 for VoxCeleb models. Preserve both notices and
  attribution; do not invent an either/or licensing choice. Neither declaration
  contains a noncommercial limitation. Original weights are unchanged.
- Component evidence: `deliverables/commercial-uid-notices-20260917-r1/`.
  This does not relicense Intel, Microsoft, the other models, or the previous
  VoxBlink checkpoint. The old signed package remains unchanged.

## Selection evidence

Frozen numerical settings: cosine threshold 0.56, margin 0.105, unchanged
frontend and 256-dimensional normalized embedding. No threshold fitting was
performed on this comparison panel.

| Model | Known correct / 60 | Wrong accepted identity | Unknown accepted / 65 | Model bytes | CPU inference, all 161 recordings |
| --- | ---: | ---: | ---: | ---: | ---: |
| Existing VoxBlink checkpoint | 58 | 0 | 0 | 100,865,597 | 76.621 s |
| VoxCeleb ResNet152-LM | 59 | 0 | 0 | 79,158,228 | 63.784 s |

This is a same-run CPU comparison, not an end-to-end startup or GPU/NPU speedup.
The model is 21.5% smaller; the recorded inference run was 16.8% shorter.
ResNet34-LM was also evaluated and was not selected: it rejected one additional
known-speaker trial relative to the existing recognizer. Results and failures
remain in their respective immutable evidence directories.

The 125 decisions are a small recorded selection panel. Enrollment excerpts
and query chapters are disjoint, but these results were used to select the
candidate. They are not a new blind evaluation, a measured population false
accept rate, resistance to replay/deepfake attacks, or human-level certification.
Zero false accepts in 65 trials must never be reported as zero general risk.

## Native integration and usable enrollment

- All 161 native embeddings agree with the independent Python reference:
  maximum absolute delta `7.82e-8`, minimum cosine greater than
  `0.9999999999999` in this run.
- Invalid input is refused, cancellation remains independent of inference,
  cancelled work retires, and fresh work produces the exact recovery result.
- One guided capture per enrolled person passes the same 60 known / 65 unknown
  panel at 59 correct, no wrong identities and no unknown accepts. The fixture
  concatenates three original public excerpts into one capture operation; it
  is not new physical enrollment evidence.
- The complete Mac, Ubuntu 24.04, and Windows 11 VM SDK journeys pass: explicit capture, close the
  microphone, persist the pending capture, restart the process, confirm the
  same handle, publish/read back enrollment, identify known/unknown speech,
  interrupt and recover, Finish, and thirteen settings cases with unchanged
  TTS PCM. This is recorded speech through the actual SDK with fixture storage,
  not an installed browser consent-card proof.

## Existing private-checkpoint enrollments

An embedding is specific to its model/frontend. A new model cannot safely use
the previous model's enrolled vectors even though both have 256 dimensions.
The registry therefore binds exact model hash, byte count, and frontend policy.
The old three-recording-to-one-recording transition remains limited to the
same model. It must not be stretched into cross-model migration.

Fresh installs can enroll directly. Existing private-checkpoint users need a
fresh operator-confirmed recording. Do not erase, reinterpret, or automatically
reset their enrollment. Before changing an existing installation, provide an
explicit backup/reset/re-enrollment path through the existing private-storage
owner; preserve unresolved state. The candidate has not been installed over
Test Identity A or Test Identity B. Model-mismatch refusal is safe but is not, by itself, a finished
upgrade UX.

UID is an attribution/filtering input, not authentication or command authority.
The host's `only` and `ignore` policy must remain fail-closed while attribution
is unknown or enrollment is unavailable, and must not leak restricted partials.

## Promotion boundary

All three desktop candidates now pass the bounded SDK gate and independent
evidence readback. Windows native contracts total 36 (3 ASR, 2 UID, 31 session),
with no exclusions. All 13 settings waveforms match the previous accelerated
Windows checkpoint, and all recorded test owners are retired. Evidence:
`deliverables/commercial-uid-windows-20260917-r1/independent-audit.json`;
runtime manifest `f094375230223924402c7f6995e11ff3005099dd02cfff0a2e284360cb2cb214`.

Windows publisher signing and all three final-signed-byte execution gates pass.
Nine engine/carrier images have timestamped AIII signatures; unchanged signed
components were independently verified rather than re-signed. Independent audit:
`deliverables/commercial-uid-signed-windows-20260917-r1/independent-audit.json`.
The Windows VM's observed cold start was 51.945 seconds, warm first PCM
202.526–315.414 ms, and guided Abort admission 2.665 ms. These are recorded-input
fixtures, not bare-metal predictions or browser mouth-to-ear measurements.
All three runtime archives are packed with the SDK and inventory-verified.

The replacement first shipped in this **staged** unified `0.1.0-beta.2` candidate:
`deliverables/desktop-beta2-signed-publication-20260917-r1/`.
Signed archive SHA-256:
`8b822e2c6d1e8693ffd7fa2728d060f3408e6b2bdb385cecb8eb236a24848412`.
The clean current AII OS verifier at `93ccab9c` accepts its new T3 signature and
rejects appended-byte tampering. All 17 pinned upstream model downloads were
freshly fetched anonymously and fully hashed; the 12 local release assets and
the catalog entry bind the final signed archive. Public URLs for the new release
are destinations, not a claim that beta.2 has been uploaded.

The current publication handoff supersedes that archive with the notice-complete
signed package at `deliverables/desktop-beta2-notice-publication-20260917-r1/`,
SHA-256 `4e6c215fb6bec5ec01031c4b0b0229d09104e969834bcf5b4ad4ff3ca1083479`.
Only the Intel notice index/texts and signature changed. Exact Intel OpenMP DLL
redistribution provenance is now resolved; engine/model/interface bytes are
unchanged. The earlier package's execution evidence remains explicitly bound
to that earlier archive rather than relabeled as new installed qualification.

The same UID model now passes physical Pixel native CPU/GPU comparison:
all 161 embeddings and 125 speaker decisions remain within unchanged gates in
four runs. GPU model inference takes 74.75% less time by ratio of means, with
actual per-record execution timestamps. The converter's fixed-length variance
defect was repaired without changing weights. The later real five-model Pixel
integration now executes this UID owner with GPU recognition, Vulkan TTS,
VAD/endpointing, interruption, exact final input tail, recovery and retirement.
It is not mobile release qualification: the unchanged strict-text gate fails
on `17` versus `seventeen`, and recovery TTS takes 8.828 seconds for four seconds
of audio (RTF 2.207). See
`deliverables/pixel-five-model-commercial-uid-20260917-r3/README.md` for the
separate component/integration/physical-audio boundaries and exact restoration.

Next: host/browser guided enrollment and UID-filter proof, safe existing-profile
upgrade, final fresh-cache installed journeys, and outstanding redistribution
terms. The old-model reset operation is not a cross-model recovery mechanism:
it first requires the current model binding, and even an old-engine reset leaves
an old-policy empty document. Preserve the originals and restrictive UID policy.
No public upload or live identity change was made. Mobile remains unqualified
for this candidate. See `DESKTOP_BETA2_CHECKPOINT_20260917.md` for the exact handoff.
