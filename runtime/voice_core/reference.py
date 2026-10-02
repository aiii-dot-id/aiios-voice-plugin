"""Deterministic standalone mock used to prove the Voice Core boundary."""

from __future__ import annotations

import hashlib
from typing import Any


def build_reference_trace(
    *,
    session_id: str,
    transcript: str,
    system_manifest_sha256: str,
    synthesis_samples: int,
    input_samples: int = 2400,
) -> dict[str, Any]:
    """Build a complete replay trace without a model or AII OS dependency."""
    if input_samples < 2 or synthesis_samples < 1:
        raise ValueError("reference sample counts must be positive and input >= 2")
    events: list[dict[str, Any]] = []

    def emit(event_type: str, **fields: Any) -> None:
        sequence = len(events) + 1
        events.append(
            {
                "sequence": sequence,
                "event_id": f"event-{sequence:03d}",
                "type": event_type,
                "observed_monotonic_ns": 1_000_000_000 + sequence * 10_000_000,
                **fields,
            }
        )

    emit("session_start")
    emit("state_reset", scope="session")
    emit(
        "input_audio",
        content_sha256=hashlib.sha256(bytes(input_samples * 2)).hexdigest(),
        stream_id="microphone",
        start_sample=0,
        end_sample=input_samples,
    )
    emit(
        "speech_start",
        activity_id="activity-1",
        stream_id="microphone",
        start_sample=0,
        end_sample=1,
    )
    emit(
        "vad_probability",
        probability=0.99,
        stream_id="microphone",
        start_sample=0,
        end_sample=input_samples,
    )
    emit(
        "transcript_partial",
        utterance_id="utterance-1",
        text=transcript[:5] or transcript,
        revision=1,
        stable_prefix_chars=0,
        stream_id="microphone",
        start_sample=0,
        end_sample=max(1, input_samples // 2),
    )
    emit(
        "speech_end",
        activity_id="activity-1",
        stream_id="microphone",
        start_sample=input_samples - 1,
        end_sample=input_samples,
    )
    emit(
        "transcript_final",
        utterance_id="utterance-1",
        text=transcript,
        revision=2,
        stream_id="microphone",
        start_sample=0,
        end_sample=input_samples,
    )
    emit(
        "diarization_span",
        speaker_track_id="track-1",
        stream_id="microphone",
        start_sample=0,
        end_sample=input_samples,
    )
    emit(
        "identity_enrolled",
        speaker_track_id="track-1",
        identity_id="identity-7",
        enrollment_id="enrollment-7",
    )
    emit(
        "identity_result",
        confidence=0.93,
        decision="known",
        identity_id="identity-7",
        speaker_track_id="track-1",
    )
    emit(
        "spoof_result",
        confidence=0.98,
        decision="bona_fide",
        speaker_track_id="track-1",
    )
    emit("turn_proposed", turn_event_id="turn-1")
    emit("turn_committed", turn_event_id="turn-1")
    emit("synthesis_start", synthesis_id="synthesis-1")
    emit(
        "audio_chunk",
        content_sha256=hashlib.sha256(bytes(synthesis_samples * 4)).hexdigest(),
        synthesis_id="synthesis-1",
        stream_id="synthesis",
        start_sample=0,
        end_sample=synthesis_samples,
    )
    emit("synthesis_end", synthesis_id="synthesis-1")
    emit("playback_start", synthesis_id="synthesis-1")
    emit("application_event", kind="tool_call_started", call_id="call-1")
    emit("application_event", kind="tool_call_completed", call_id="call-1")
    emit("playback_stop", synthesis_id="synthesis-1")
    emit("application_event", kind="task_complete")
    emit("session_end", status="completed")

    return {
        "schema": "aiii.voice.core.trace",
        "schema_version": 1,
        "session_id": session_id,
        "system": {
            "system_manifest_sha256": system_manifest_sha256,
            "runtime": {
                "name": "aii-voice-core",
                "revision": "standalone-contract",
                "backend": "python-reference",
                "precision": "reference",
                "settings_sha256": "e" * 64,
            },
        },
        "audio_streams": [
            {
                "stream_id": "microphone",
                "direction": "input",
                "sample_rate_hz": 24000,
                "channels": 1,
                "sample_type": "pcm_s16le",
            },
            {
                "stream_id": "playback-reference",
                "direction": "input",
                "sample_rate_hz": 24000,
                "channels": 1,
                "sample_type": "pcm_s16le",
            },
            {
                "stream_id": "synthesis",
                "direction": "output",
                "sample_rate_hz": 24000,
                "channels": 1,
                "sample_type": "pcm_f32le",
            },
        ],
        "timing": {
            "mode": "replay",
            "arrival_rate": 0.0,
            "latency_claims": False,
        },
        "events": events,
    }
