# Standalone speaker identity

This component turns the pinned WeSpeaker model into an actual persistent
enrollment and open-set recognition API. It has no AII OS, Plugin SDK, network,
microphone or TTS dependency. It never enrolls someone implicitly. Labels are
caller-supplied enrollment names, not identities inferred from personal data.

Operations: **create, enroll, identify, list, remove, reset**. Recognition returns
**known, unknown or ambiguous**, not a forced nearest neighbour. Scores are
cosine similarities, **not calibrated probabilities**. This is not liveness,
spoof detection, diarization, overlap detection or authorization. A host must
not grant permissions based on it.

## Library interface

The package is `runtime.speaker_identity` with this project on `PYTHONPATH`.
Inference requires the existing `numpy`, `kaldi-native-fbank` and the target's
ONNX Runtime provider package. It does not download/install models at runtime.

```python
from pathlib import Path
import json
from runtime.speaker_identity import IdentityStore, Policy
from runtime.speaker_identity.backend import WeSpeaker

policy = Policy(**json.loads(Path("policy.json").read_text()))
model = WeSpeaker(
    Path("voxblink2_samresnet34_ft.onnx"),
    Path("voxblink2_samresnet34_ft.fixed-198.onnx"),
    Path("fixed-198.json"),
    provider="coreml",  # explicit cuda / directml / cpu are also supported
)

# Once, by an explicit enrollment action; reopening never creates a missing DB.
with IdentityStore.create(Path("private/voices.sqlite"), policy) as voices:
    recording = model.embed(Path("enrollment.wav"))
    voices.enroll(
        "speaker-1",
        "Operator-chosen label",
        recording.vector,
        audio_sha256=recording.audio_sha256,
        embedding_binding=recording.embedding_binding,
    )

# Another recording, possibly in another application process.
with IdentityStore(Path("private/voices.sqlite"), policy) as voices:
    recording = model.embed(Path("new-utterance.wav"))
    decision = voices.identify(
        recording.vector,
        embedding_binding=recording.embedding_binding,
    )
    print(decision.outcome, decision.speaker_id)
    voices.remove("speaker-1")  # also removes that speaker's stored vectors
```

Model construction and `embed` are synchronous work. Keep one resident model on
a bounded inference worker; do **not** execute this work on the native capture,
playback-stop or cancellation lane. Each SQLite connection belongs to one thread.
Separate connections/processes may use the same store. Identification reports
the revision of its consistent enrollment snapshot. A host should bind the
returned result to its session/utterance and discard stale results before
publishing an identity event. **That live session adapter is not implemented by
this standalone file-based API.** The working native conversation is unchanged.

## Command line

From the workspace root, using the existing project Python:

```sh
python -m runtime.speaker_identity \
  --database private/voices.sqlite --policy policy.json create

python -m runtime.speaker_identity \
  --database private/voices.sqlite --policy policy.json \
  --source-model artifacts/wespeaker-uid/voxblink2_samresnet34_ft.onnx \
  --model artifacts/wespeaker-uid/voxblink2_samresnet34_ft.fixed-198.onnx \
  --specialization artifacts/wespeaker-uid/fixed-198.json \
  --provider coreml enroll speaker-1 --label 'Operator-chosen label' --wav enrollment.wav

# Use the same global arguments for: identify --wav query.wav
# These operations need no model load:
python -m runtime.speaker_identity --database private/voices.sqlite --policy policy.json list
python -m runtime.speaker_identity --database private/voices.sqlite --policy policy.json remove speaker-1
python -m runtime.speaker_identity --database private/voices.sqlite --policy policy.json reset --confirm
```

`policy.json` is an exact measured development operating point from the
[delivery evidence](../../deliverables/speaker-identity/README.md), not a default
security policy. **All three measured operating points remain accuracy-unqualified.**
The latest three-recording policy is usable for explicit development evaluation,
not automatic live identity assignment. It enforces at least three distinct
recordings before returning a known identity; earlier enrollments are collecting.
A new policy
or model/frontend binding requires explicit compatible enrollment reconstruction;
an existing DB will not silently change its meaning.

## Audio and inference contract

- Input: a complete **16 kHz mono PCM16 WAV**, 1.995–30 seconds, with actual
  payload length checked. Too-short, overlong, silent/near-silent, heavily
  clipped, malformed and non-regular inputs are refused. There is no implicit
  resampling or silent truncation. The amplitude screen is not a speech/overlap
  classifier: supply a VAD/turn-selected single-speaker utterance.
- Exact original model SHA-256:
  `33af8affe6191b1ebd196d2b56e22c2934104cd2764abfdbdd954d3a934eb2a1`.
  Fixed-198 shape copy:
  `01a3d21cb934bfaa668c3e8e6fba20e65ba038637909b3ceb52a67796e88ea7c`.
  No learned tensors were updated. `scripts/specialize_wespeaker_onnx_shape.py`
  produces this additive copy; the original model is never overwritten.
- Kaldi-compatible 80-bin fbank with utterance CMN is the shared WeSpeaker
  frontend used by the old evaluation runner and this component. Four retained
  feature vectors pin it byte-for-byte. This model-specific frontend is **not**
  interchangeable with the generic causal frontend used by other voice models.
- Fixed windows are 198 frames; the final overlapping window preserves the
  tail. Window embeddings are normalized then averaged and normalized again.
  Utterance CMN requires complete input: this does **not** claim streaming UID
  or a two-second wall-clock response deadline.
- Core ML requests `CPUAndGPU`, MLProgram and static shapes. CUDA disables TF32;
  DirectML uses sequential execution and disables memory patterns. A missing
  requested provider fails rather than selecting a new one. Runtime placement
  still requires execution/profile evidence. A registered provider name alone
  does not prove GPU execution; Core ML compute plans are not hardware traces.

## Persistence, concurrency and limits

One SQLite file is the enrollment authority, not an additional identity ledger.
Model, frontend and measured policy fingerprints must match. Every enrollment,
removal and reset commits its revision with the vector changes. Duplicate audio
is refused even under another identity name. Changed labels do not silently
replace identities. Interrupted or rejected transactions cannot leave a new
speaker without its evidence. Zero/nonfinite embeddings and unusable centroids
fail closed.

Bounds: 256 speakers, eight enrollment samples per speaker, 30-second utterance,
two-second SQLite busy timeout. These bounds are not quality qualifications at
every gallery size. The measured policy's current evidence is narrower. Multiple
samples improve coverage only when they are genuinely useful independent voice
observations; different file names are not counted as new evidence.

The store keeps vectors and hashes of decoded enrollment PCM, not audio or
transcripts. Newly created files are mode 0600; `secure_delete` and foreign-key
deletion remove vectors on remove/reset. This is **not** encryption or guaranteed
forensic erasure from backups, filesystem snapshots or storage hardware. The host
owns access control, retention and any secure-at-rest requirement.

## Qualification boundary

Use the exact results and failure history in the delivery record. Public recorded
speech and process-restart proofs do not establish real room/device accuracy,
replay resistance or population FAR/FRR. The native page does not yet automatically
assign these identities. Linux/Windows/phone provider support in this wrapper is
an implementation surface; old model-only vectors are not full component ports.
