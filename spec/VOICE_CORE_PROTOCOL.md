# AII Voice Core streaming protocol

Status: executable pre-release contract.

This protocol is the standalone boundary between audio/model execution and any
consumer. It is not an AII OS protocol. AII OS integration translates this
boundary without redefining it. Until the first release, the current contract
is corrected in place rather than preserving artificial legacy variants.

The executable authority is `runtime/voice_core/protocol.py`. A JSON document
is admissible only when that validator accepts it.

## Contract layers

There are three authorities with one dependency direction:

1. evidence and training samples preserve source material and labels;
2. Voice Core defines the normative live runtime lifecycle;
3. benchmark and evaluation adapters translate external corpora into Voice
   Core observations without redefining runtime semantics.

Turn-control and duplex-agent files are evaluation adapters. Their schedule and
tool laws must use the shared Voice Core validators rather than become competing
runtime protocols.

## Trace envelope

```json
{
  "schema": "aiii.voice.core.trace",
  "schema_version": 1,
  "session_id": "session-opaque-id",
  "system": {
    "system_manifest_sha256": "64 lowercase hexadecimal characters",
    "runtime": {
      "name": "standalone-reference",
      "revision": "immutable revision",
      "backend": "declared execution backend",
      "precision": "declared precision",
      "settings_sha256": "64 lowercase hexadecimal characters"
    }
  },
  "audio_streams": [],
  "timing": {
    "mode": "live_arrival",
    "arrival_rate": 1.0,
    "clock": "time.monotonic_ns",
    "origin_monotonic_ns": 1000000000,
    "latency_claims": true
  },
  "events": []
}
```

The complete system manifest binds every component, frontend, calibration,
threshold, runtime setting, conversion, and target. A single model hash cannot
identify a modular or hybrid system.

`live_arrival` is the only mode allowed to claim latency. Input audio is checked
against the exclusive final-sample boundary derived from the declared stream
sample rate and monotonic origin. Accelerated and replay traces carry behavioral
evidence only.

## Common laws

Every event contains a contiguous sequence, unique event id, registered type,
and positive nondecreasing observation time. Unknown fields are refused rather
than silently accepted.

The first event is `session_start`; the second is exactly one session
`state_reset`; the final event is exactly one `session_end`, `failure`, or
`cancellation`. A successful end refuses unresolved speech, transcript, turn,
synthesis, playback, or tool lifecycles.

Audio spans are start-inclusive/end-exclusive and have positive length. Input
and playback-reference chunks are gapless and non-overlapping per stream.
Generated chunks are gapless per synthesis and output stream. Input events may
use only input streams and generated chunks only output streams. Byte-bearing
input and output chunks carry full content SHA-256 values so a recorded trace
can be checked against the byte objects in its evidence bundle. The trace binds
those objects; it does not claim that a digest alone can reconstruct them.

## Event families

### Audio, activity, and recognition

- `input_audio`, `playback_reference`: gapless byte-bound input spans;
- `vad_probability`: bounded acoustic evidence;
- `speech_start`, `speech_end`: paired activities;
- `transcript_partial`, `transcript_final`: monotonically revised utterances
  with source spans and optional nondecreasing stable-prefix length;
  partials are revisable hypotheses, not an action channel. A final may contain
  an empty string to explicitly withdraw the hypothesis; it resolves that
  utterance, clears its preview, and must not commit a response or add a blank
  conversation-history entry. A partial itself remains nonempty.
- `diarization_span`: source span establishing a session speaker track;
- `speaker_overlap`: a span naming at least two already-established tracks.

### Persistent identity and spoof detection

- `identity_enrolled`: connects an established session track to a persistent
  identity and enrollment id;
- `identity_withdrawn`: explicitly removes an enrolled persistent identity;
- `identity_result`: known, unknown, or ambiguous decision for an established
  track. Known results may name only an enrolled identity;
- `spoof_result`: bona-fide, spoof, or uncertain decision for an established
  track. A spoof decision names its attack type.

Diarization tracks are session-local observations. They never imply persistent
identity. UID is open-set personalization, not authentication.

### Turn, synthesis, playback, and application

- `turn_proposed`, `turn_revoked`, `turn_committed`: one exact proposal
  lifecycle;
- `synthesis_start`, `audio_chunk`, `synthesis_end`: one byte-bound synthesis
  lifecycle. `synthesis_cancelled` with a non-empty `reason` resolves only that
  synthesis without falsely completing it or cancelling the whole session.
  Further chunks and reuse of its synthesis id are refused;
- `playback_start`, `playback_stop`: playback lifecycle. Client acknowledgements
  establish client-reported state, not physical DAC timing or audible output.
  Those stronger claims require independently qualified playback evidence;
- `interruption_requested`: names a known synthesis and a non-empty cause.
  It records the request, not proof that audible playback has stopped. It may
  target queued playback after synthesis generation has already ended;
- `application_event`: registered response, task, and tool-call lifecycle.

Tool calls are paired by `call_id`. A final prose response is not a tool result,
and a successful session cannot conceal an unfinished tool call.

Failure and cancellation may truthfully terminate open lifecycles. Success may
not.

## Compatibility and conformance

Consumers never ignore unknown semantics. After release, changing required
meaning requires an explicit compatibility ruling. Before release, fixtures and
implementations move together to the one current contract.

Validate without AII OS:

```sh
python scripts/validate_voice_core_trace.py TRACE.json
```

Acceptance establishes structural, identity, pacing, and lifecycle truth. It
does not establish model quality, human-level behavior, physical-platform
support, or acceptable latency.

### Deterministic replay

`runtime/voice_core/replay.py` defines the first backend-conformance seam. A
replay bundle embeds one canonical trace and every byte object named by an
`input_audio`, `playback_reference`, or `audio_chunk` event. The runner:

1. verifies the canonical trace SHA-256 and the complete trace contract;
2. requires `timing.mode=replay` and `latency_claims=false`;
3. verifies canonical base64, byte counts, and content SHA-256 values;
4. checks byte length against each stream's sample type, channels, and span;
5. refuses missing, duplicate, and unreferenced content objects;
6. derives input boundaries through the shared source-sample clock;
7. drives start, reset, ordered-consume, and terminal backend callbacks; and
8. requires an exact receipt over every replayed observation.

Replay never sleeps or substitutes event observation timestamps for live
arrival. Its sample-boundary offsets preserve ordering semantics for adapters;
they are not latency measurements. Backend failure is attributed as replay
failure, while valid `failure` and `cancellation` trace terminals remain
truthful outcomes.

The included digest backend proves the adapter contract, reset propagation,
content custody, and exact consumption. It is not a model backend and cannot
establish inference correctness or quality. A real backend earns conformance
only when its adapter produces the same complete receipt while exercising its
actual state and event path.

The first real adapter is `runtime/voice_core/silero_mlx_backend.py`. Its exact
binding commits to repository revision, weight and configuration identities,
installed MLX and MLX Audio versions, dtype, sample rate, chunk size, and
probability tolerance. Replay requires one stateful model output for every
512-sample input span and compares it with the immediately paired
`vad_probability`; missing, extra, reordered, or numerically drifting outputs
fail. The committed M3 conformance bundle runs eight sequential chunks through
the strict pinned model twice and records the exact probability fingerprint.
This proves deterministic stateful backend wiring for that artifact and
runtime. It is not a VAD quality, live-arrival, latency, resource, platform, or
owned-model result.
