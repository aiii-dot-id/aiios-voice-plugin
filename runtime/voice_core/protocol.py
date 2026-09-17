"""Executable validator for the standalone AII Voice Core event protocol."""

from __future__ import annotations

import math
import re
from typing import Any

from .timing import sample_schedule_lag_ms

SCHEMA = "aiii.voice.core.trace"
SCHEMA_VERSION = 1
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SAMPLE_TYPES = {"pcm_s16le", "pcm_f32le"}
TIMING_MODES = {"live_arrival", "accelerated", "replay"}
TERMINALS = {"session_end", "failure", "cancellation"}
APPLICATION_KINDS = {
    "response_committed",
    "task_complete",
    "tool_call_started",
    "tool_call_completed",
    "tool_call_failed",
}
TOOL_START = "tool_call_started"
TOOL_TERMINALS = {"tool_call_completed", "tool_call_failed"}
EVENT_TYPES = {
    "session_start",
    "state_reset",
    "input_audio",
    "playback_reference",
    "vad_probability",
    "speech_start",
    "speech_end",
    "speaker_overlap",
    "transcript_partial",
    "transcript_final",
    "diarization_span",
    "identity_enrolled",
    "identity_withdrawn",
    "identity_result",
    "spoof_result",
    "turn_proposed",
    "turn_revoked",
    "turn_committed",
    "synthesis_start",
    "audio_chunk",
    "synthesis_end",
    "synthesis_cancelled",
    "interruption_requested",
    "playback_start",
    "playback_stop",
    "application_event",
    *TERMINALS,
}
COMMON_EVENT_FIELDS = {"sequence", "event_id", "type", "observed_monotonic_ns"}
SPAN_FIELDS = {"stream_id", "start_sample", "end_sample"}
EVENT_REQUIRED_FIELDS = {
    "session_start": set(),
    "state_reset": {"scope"},
    "input_audio": SPAN_FIELDS | {"content_sha256"},
    "playback_reference": SPAN_FIELDS | {"content_sha256"},
    "vad_probability": SPAN_FIELDS | {"probability"},
    "speech_start": SPAN_FIELDS | {"activity_id"},
    "speech_end": SPAN_FIELDS | {"activity_id"},
    "speaker_overlap": SPAN_FIELDS | {"speaker_track_ids"},
    "transcript_partial": SPAN_FIELDS | {"utterance_id", "text", "revision"},
    "transcript_final": SPAN_FIELDS | {"utterance_id", "text", "revision"},
    "diarization_span": SPAN_FIELDS | {"speaker_track_id"},
    "identity_enrolled": {"speaker_track_id", "identity_id", "enrollment_id"},
    "identity_withdrawn": {"identity_id"},
    "identity_result": {"speaker_track_id", "decision", "confidence"},
    "spoof_result": {"speaker_track_id", "decision", "confidence"},
    "turn_proposed": {"turn_event_id"},
    "turn_revoked": {"turn_event_id"},
    "turn_committed": {"turn_event_id"},
    "synthesis_start": {"synthesis_id"},
    "audio_chunk": SPAN_FIELDS | {"synthesis_id", "content_sha256"},
    "synthesis_end": {"synthesis_id"},
    "synthesis_cancelled": {"synthesis_id", "reason"},
    "interruption_requested": {"synthesis_id", "reason"},
    "playback_start": {"synthesis_id"},
    "playback_stop": {"synthesis_id"},
    "application_event": {"kind"},
    "session_end": {"status"},
    "failure": {"reason"},
    "cancellation": {"reason"},
}
EVENT_OPTIONAL_FIELDS = {
    "transcript_partial": {"stable_prefix_chars"},
    "identity_result": {"identity_id"},
    "spoof_result": {"attack_type"},
    "application_event": {"call_id", "payload_sha256"},
}


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _exact_fields(
    value: dict[str, Any], *, required: set[str], optional: set[str], label: str
) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required - optional)
    if missing:
        raise ValueError(f"{label} lacks required fields: {missing}")
    if unknown:
        raise ValueError(f"{label} has unknown fields: {unknown}")


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _integer(value: Any, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _probability(value: Any, label: str) -> float:
    result = _number(value, label)
    if result < 0.0 or result > 1.0:
        raise ValueError(f"{label} must be a number in [0, 1]")
    return result


def _validate_system(value: Any) -> None:
    system = _object(value, "system")
    _exact_fields(
        system,
        required={"system_manifest_sha256", "runtime"},
        optional=set(),
        label="system",
    )
    _sha256(system["system_manifest_sha256"], "system.system_manifest_sha256")
    runtime = _object(system["runtime"], "system.runtime")
    _exact_fields(
        runtime,
        required={"name", "revision", "backend", "precision", "settings_sha256"},
        optional=set(),
        label="system.runtime",
    )
    for field in ("name", "revision", "backend", "precision"):
        _string(runtime[field], f"system.runtime.{field}")
    _sha256(runtime["settings_sha256"], "system.runtime.settings_sha256")


def _validate_streams(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("audio_streams must be a non-empty array")
    result: dict[str, dict[str, Any]] = {}
    required = {"stream_id", "direction", "sample_rate_hz", "channels", "sample_type"}
    for index, raw in enumerate(value):
        label = f"audio_streams[{index}]"
        stream = _object(raw, label)
        _exact_fields(stream, required=required, optional=set(), label=label)
        stream_id = _string(stream["stream_id"], f"{label}.stream_id")
        if stream_id in result:
            raise ValueError(f"duplicate audio stream id: {stream_id}")
        if stream["direction"] not in {"input", "output"}:
            raise ValueError(f"{label}.direction must be input or output")
        _integer(stream["sample_rate_hz"], f"{label}.sample_rate_hz", 1)
        _integer(stream["channels"], f"{label}.channels", 1)
        if stream["sample_type"] not in SAMPLE_TYPES:
            raise ValueError(f"{label}.sample_type is unsupported")
        result[stream_id] = stream
    return result


def _validate_timing(value: Any) -> tuple[bool, int | None]:
    timing = _object(value, "timing")
    _exact_fields(
        timing,
        required={"mode", "arrival_rate", "latency_claims"},
        optional={"clock", "origin_monotonic_ns"},
        label="timing",
    )
    mode = timing["mode"]
    if mode not in TIMING_MODES:
        raise ValueError(f"timing.mode must be one of {sorted(TIMING_MODES)}")
    rate = _number(timing["arrival_rate"], "timing.arrival_rate")
    if not isinstance(timing["latency_claims"], bool):
        raise ValueError("timing.latency_claims must be boolean")
    if mode == "live_arrival":
        if rate != 1.0:
            raise ValueError("live_arrival requires exact arrival_rate 1.0")
        if timing.get("clock") != "time.monotonic_ns":
            raise ValueError("live_arrival requires time.monotonic_ns")
        origin = _integer(
            timing.get("origin_monotonic_ns"), "timing.origin_monotonic_ns", 1
        )
    else:
        if timing["latency_claims"]:
            raise ValueError("only live_arrival may make latency claims")
        if "origin_monotonic_ns" in timing or "clock" in timing:
            raise ValueError(f"{mode} timing cannot declare a live clock or origin")
        if mode == "accelerated" and rate <= 1.0:
            raise ValueError("accelerated timing requires arrival_rate > 1.0")
        if mode == "replay" and rate < 0.0:
            raise ValueError("replay timing requires arrival_rate >= 0")
        origin = None
    return bool(timing["latency_claims"]), origin


def _validate_event_shape(event: dict[str, Any], kind: str, label: str) -> None:
    _exact_fields(
        event,
        required=COMMON_EVENT_FIELDS | EVENT_REQUIRED_FIELDS[kind],
        optional=EVENT_OPTIONAL_FIELDS.get(kind, set()),
        label=label,
    )


def _audio_span(
    event: dict[str, Any],
    label: str,
    streams: dict[str, dict[str, Any]],
    *,
    direction: str,
) -> tuple[str, int, int]:
    stream_id = _string(event["stream_id"], f"{label}.stream_id")
    if stream_id not in streams:
        raise ValueError(f"{label}.stream_id names an undeclared stream: {stream_id}")
    if streams[stream_id]["direction"] != direction:
        raise ValueError(f"{label} requires an {direction} stream: {stream_id}")
    start = _integer(event["start_sample"], f"{label}.start_sample")
    end = _integer(event["end_sample"], f"{label}.end_sample")
    if end <= start:
        raise ValueError(f"{label} audio interval must have positive length")
    return stream_id, start, end


def _validate_application_event(
    event: dict[str, Any], label: str, open_calls: set[str], closed_calls: set[str]
) -> None:
    kind = event["kind"]
    if kind not in APPLICATION_KINDS:
        raise ValueError(f"{label}.kind is unknown: {kind!r}")
    payload = event.get("payload_sha256")
    if payload is not None:
        _sha256(payload, f"{label}.payload_sha256")
    call_id = event.get("call_id")
    if kind in {TOOL_START, *TOOL_TERMINALS}:
        call_id = _string(call_id, f"{label}.call_id")
    elif call_id is not None:
        raise ValueError(f"{label}.call_id is valid only for tool lifecycle events")
    if kind == TOOL_START:
        if call_id in open_calls or call_id in closed_calls:
            raise ValueError(f"duplicate tool call: {call_id}")
        open_calls.add(call_id)
    elif kind in TOOL_TERMINALS:
        if call_id not in open_calls:
            raise ValueError(f"{kind} has no open tool call: {call_id}")
        open_calls.remove(call_id)
        closed_calls.add(call_id)


def validate_trace(trace: Any) -> dict[str, Any]:
    """Validate one complete standalone trace and return a structural summary."""
    root = _object(trace, "trace")
    raw_events = root.get("events")
    if not isinstance(raw_events, list) or len(raw_events) < 3:
        raise ValueError(
            "events must contain session_start, state_reset, and a terminal"
        )
    return _validate_trace(root)


def validate_event_stream(metadata: dict[str, Any], events) -> dict[str, Any]:
    """Use the same laws for a one-pass journal, without retaining PCM events."""
    if "events" in metadata:
        raise ValueError("event stream metadata cannot contain events")
    return _validate_trace({**metadata, "events": events})


def _body_events(raw_events, state):
    event_ids = set()
    observed = 0
    for index, raw in enumerate(raw_events):
        label = f"events[{index}]"
        event = _object(raw, label)
        if event.get("sequence") != index + 1:
            raise ValueError(f"{label}.sequence must equal {index + 1}")
        event_id = _string(event.get("event_id"), f"{label}.event_id")
        if event_id in event_ids:
            raise ValueError(f"duplicate event_id: {event_id}")
        event_ids.add(event_id)
        kind = event.get("type")
        if kind not in EVENT_TYPES:
            raise ValueError(f"{label}.type is unknown: {kind!r}")
        _validate_event_shape(event, kind, label)
        now = _integer(
            event["observed_monotonic_ns"], f"{label}.observed_monotonic_ns", 1
        )
        if now < observed:
            raise ValueError("events are not in monotonic observation order")
        observed = now
        state["count"] = index + 1
        state["spoof_results"] += kind == "spoof_result"
        if state["terminal"] is not None:
            raise ValueError(
                "trace must contain exactly one terminal event in final position"
            )
        if index == 0:
            if kind != "session_start":
                raise ValueError("first event must be session_start")
            continue
        if index == 1:
            if kind != "state_reset" or event["scope"] != "session":
                raise ValueError(
                    "session_start must be followed by a session state_reset"
                )
            continue
        if kind == "state_reset":
            raise ValueError(
                "session state_reset may occur exactly once after session_start"
            )
        if kind in TERMINALS:
            if kind == "session_end":
                if event["status"] != "completed":
                    raise ValueError("session_end.status must equal completed")
            else:
                _string(event["reason"], f"{kind}.reason")
            state["terminal"] = event
        else:
            yield index, event
    if state["count"] < 3 or state["terminal"] is None:
        raise ValueError(
            "trace must contain exactly one terminal event in final position"
        )


def _validate_trace(trace: Any) -> dict[str, Any]:
    root = _object(trace, "trace")
    _exact_fields(
        root,
        required={
            "schema",
            "schema_version",
            "session_id",
            "system",
            "audio_streams",
            "timing",
            "events",
        },
        optional=set(),
        label="trace",
    )
    if root["schema"] != SCHEMA:
        raise ValueError(f"trace.schema must equal {SCHEMA!r}")
    if root["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"trace.schema_version must equal {SCHEMA_VERSION}")
    _string(root["session_id"], "session_id")
    _validate_system(root["system"])
    streams = _validate_streams(root["audio_streams"])
    latency_claims, live_origin = _validate_timing(root["timing"])

    activities: set[str] = set()
    final_utterances: set[str] = set()
    seen_utterances: set[str] = set()
    utterance_revisions: dict[str, int] = {}
    stable_prefixes: dict[str, int] = {}
    speaker_tracks: set[str] = set()
    enrolled_identities: set[str] = set()
    open_turns: set[str] = set()
    resolved_turns: set[str] = set()
    open_synthesis: set[str] = set()
    closed_synthesis: set[str] = set()
    cancelled_synthesis: set[str] = set()
    playing: set[str] = set()
    open_calls: set[str] = set()
    closed_calls: set[str] = set()
    input_positions: dict[tuple[str, str], int] = {}
    output_positions: dict[tuple[str, str], int] = {}
    max_schedule_lag = None
    state = {"terminal": None, "count": 0, "spoof_results": 0}

    for index, event in _body_events(root["events"], state):
        kind = event["type"]
        label = f"events[{index}]"
        if kind in {"input_audio", "playback_reference"}:
            stream_id, start, end = _audio_span(
                event, label, streams, direction="input"
            )
            _sha256(event["content_sha256"], f"{label}.content_sha256")
            key = (kind, stream_id)
            expected = input_positions.get(key, 0)
            if start != expected:
                raise ValueError(
                    f"{label} must start at sample {expected}, got {start}"
                )
            input_positions[key] = end
            if live_origin is not None:
                lag = sample_schedule_lag_ms(
                    observed_monotonic_ns=event["observed_monotonic_ns"],
                    origin_monotonic_ns=live_origin,
                    end_sample=end,
                    sample_rate_hz=streams[stream_id]["sample_rate_hz"],
                )
                if lag < 0:
                    raise ValueError(
                        f"{label} was observed before its final sample arrived"
                    )
                max_schedule_lag = max(lag, max_schedule_lag or 0)
        elif kind == "vad_probability":
            _audio_span(event, label, streams, direction="input")
            _probability(event["probability"], f"{label}.probability")
        elif kind == "speech_start":
            _audio_span(event, label, streams, direction="input")
            activity = _string(event["activity_id"], f"{label}.activity_id")
            if activity in activities:
                raise ValueError(f"duplicate open speech activity: {activity}")
            activities.add(activity)
        elif kind == "speech_end":
            _audio_span(event, label, streams, direction="input")
            activity = _string(event["activity_id"], f"{label}.activity_id")
            if activity not in activities:
                raise ValueError(f"speech_end has no open activity: {activity}")
            activities.remove(activity)
        elif kind == "speaker_overlap":
            _audio_span(event, label, streams, direction="input")
            tracks = event["speaker_track_ids"]
            if not isinstance(tracks, list) or len(tracks) < 2:
                raise ValueError(
                    f"{label}.speaker_track_ids must name at least two tracks"
                )
            if not all(isinstance(track, str) and track for track in tracks):
                raise ValueError(
                    f"{label}.speaker_track_ids must contain non-empty strings"
                )
            if len(set(tracks)) != len(tracks):
                raise ValueError(f"{label}.speaker_track_ids contains duplicates")
            unknown_tracks = sorted(set(tracks) - speaker_tracks)
            if unknown_tracks:
                raise ValueError(
                    f"{label} names unknown speaker tracks: {unknown_tracks}"
                )
        elif kind in {"transcript_partial", "transcript_final"}:
            _audio_span(event, label, streams, direction="input")
            utterance = _string(event["utterance_id"], f"{label}.utterance_id")
            transcript = event["text"]
            if not isinstance(transcript, str) or (
                not transcript and kind != "transcript_final"
            ):
                raise ValueError(
                    f"{label}.text must be a string; only a final withdrawal may be empty"
                )
            revision = _integer(event["revision"], f"{label}.revision", 1)
            if utterance in final_utterances:
                raise ValueError(f"transcript observed after final: {utterance}")
            previous_revision = utterance_revisions.get(utterance, 0)
            if revision != previous_revision + 1:
                raise ValueError(
                    f"{label}.revision must equal {previous_revision + 1} for {utterance}"
                )
            utterance_revisions[utterance] = revision
            stable_prefix = event.get("stable_prefix_chars")
            if stable_prefix is not None:
                stable_prefix = _integer(stable_prefix, f"{label}.stable_prefix_chars")
                if stable_prefix > len(transcript):
                    raise ValueError(
                        f"{label}.stable_prefix_chars exceeds transcript length"
                    )
                if stable_prefix < stable_prefixes.get(utterance, 0):
                    raise ValueError(f"{label}.stable_prefix_chars cannot decrease")
                stable_prefixes[utterance] = stable_prefix
            seen_utterances.add(utterance)
            if kind == "transcript_final":
                final_utterances.add(utterance)
        elif kind == "diarization_span":
            _audio_span(event, label, streams, direction="input")
            track = _string(event["speaker_track_id"], f"{label}.speaker_track_id")
            speaker_tracks.add(track)
        elif kind == "identity_enrolled":
            track = _string(event["speaker_track_id"], f"{label}.speaker_track_id")
            if track not in speaker_tracks:
                raise ValueError(f"{label} names an unknown speaker track: {track}")
            identity = _string(event["identity_id"], f"{label}.identity_id")
            _string(event["enrollment_id"], f"{label}.enrollment_id")
            if identity in enrolled_identities:
                raise ValueError(f"duplicate identity enrollment: {identity}")
            enrolled_identities.add(identity)
        elif kind == "identity_withdrawn":
            identity = _string(event["identity_id"], f"{label}.identity_id")
            if identity not in enrolled_identities:
                raise ValueError(
                    f"identity_withdrawn names no enrolled identity: {identity}"
                )
            enrolled_identities.remove(identity)
        elif kind in {"identity_result", "spoof_result"}:
            track = _string(event["speaker_track_id"], f"{label}.speaker_track_id")
            if track not in speaker_tracks:
                raise ValueError(f"{label} names an unknown speaker track: {track}")
            _probability(event["confidence"], f"{label}.confidence")
            if kind == "identity_result":
                decision = event["decision"]
                if decision not in {"known", "unknown", "ambiguous"}:
                    raise ValueError(
                        f"{label}.decision must be known, unknown, or ambiguous"
                    )
                identity = event.get("identity_id")
                if decision == "known":
                    identity = _string(identity, f"{label}.identity_id")
                    if identity not in enrolled_identities:
                        raise ValueError(
                            f"{label} names an unenrolled identity: {identity}"
                        )
                elif identity is not None:
                    raise ValueError(
                        f"{label} cannot name identity_id for decision {decision}"
                    )
            else:
                decision = event["decision"]
                if decision not in {"bona_fide", "spoof", "uncertain"}:
                    raise ValueError(
                        f"{label}.decision must be bona_fide, spoof, or uncertain"
                    )
                attack_type = event.get("attack_type")
                if decision == "spoof":
                    _string(attack_type, f"{label}.attack_type")
                elif attack_type is not None:
                    raise ValueError(
                        f"{label}.attack_type is valid only for spoof decisions"
                    )
        elif kind == "turn_proposed":
            turn = _string(event["turn_event_id"], f"{label}.turn_event_id")
            if turn in open_turns or turn in resolved_turns:
                raise ValueError(f"duplicate turn proposal: {turn}")
            open_turns.add(turn)
        elif kind in {"turn_revoked", "turn_committed"}:
            turn = _string(event["turn_event_id"], f"{label}.turn_event_id")
            if turn not in open_turns:
                raise ValueError(f"{kind} has no open proposal: {turn}")
            open_turns.remove(turn)
            resolved_turns.add(turn)
        elif kind == "synthesis_start":
            synthesis = _string(event["synthesis_id"], f"{label}.synthesis_id")
            if (
                synthesis in open_synthesis
                or synthesis in closed_synthesis
                or synthesis in cancelled_synthesis
            ):
                raise ValueError(f"duplicate synthesis start: {synthesis}")
            open_synthesis.add(synthesis)
        elif kind == "audio_chunk":
            synthesis = _string(event["synthesis_id"], f"{label}.synthesis_id")
            if synthesis not in open_synthesis:
                raise ValueError(f"audio_chunk has no open synthesis: {synthesis}")
            stream_id, start, end = _audio_span(
                event, label, streams, direction="output"
            )
            _sha256(event["content_sha256"], f"{label}.content_sha256")
            key = (synthesis, stream_id)
            expected = output_positions.get(key, 0)
            if start != expected:
                raise ValueError(
                    f"{label} must start at output sample {expected}, got {start}"
                )
            output_positions[key] = end
        elif kind == "synthesis_end":
            synthesis = _string(event["synthesis_id"], f"{label}.synthesis_id")
            if synthesis not in open_synthesis:
                raise ValueError(f"synthesis_end has no open synthesis: {synthesis}")
            open_synthesis.remove(synthesis)
            closed_synthesis.add(synthesis)
        elif kind == "synthesis_cancelled":
            synthesis = _string(event["synthesis_id"], f"{label}.synthesis_id")
            _string(event["reason"], f"{label}.reason")
            if synthesis not in open_synthesis:
                raise ValueError(
                    f"synthesis_cancelled has no open synthesis: {synthesis}"
                )
            open_synthesis.remove(synthesis)
            cancelled_synthesis.add(synthesis)
        elif kind == "interruption_requested":
            synthesis = _string(event["synthesis_id"], f"{label}.synthesis_id")
            _string(event["reason"], f"{label}.reason")
            if synthesis not in open_synthesis | closed_synthesis | cancelled_synthesis:
                raise ValueError(
                    f"interruption_requested has no known synthesis: {synthesis}"
                )
        elif kind == "playback_start":
            synthesis = _string(event["synthesis_id"], f"{label}.synthesis_id")
            # A client may start playing audio it buffered before generation
            # was cancelled; interruption already targets such playback, and
            # playback_start models the same state.
            if synthesis not in open_synthesis | closed_synthesis | cancelled_synthesis:
                raise ValueError(f"playback_start has no known synthesis: {synthesis}")
            if synthesis in playing:
                raise ValueError(f"duplicate playback_start: {synthesis}")
            playing.add(synthesis)
        elif kind == "playback_stop":
            synthesis = _string(event["synthesis_id"], f"{label}.synthesis_id")
            if synthesis not in playing:
                raise ValueError(f"playback_stop has no active playback: {synthesis}")
            playing.remove(synthesis)
        elif kind == "application_event":
            _validate_application_event(event, label, open_calls, closed_calls)

    terminal = state["terminal"]
    if terminal["type"] == "session_end":
        unfinished = seen_utterances - final_utterances
        open_state = {
            "speech activities": activities,
            "non-final transcripts": unfinished,
            "turn proposals": open_turns,
            "syntheses": open_synthesis,
            "playback": playing,
            "tool calls": open_calls,
        }
        for name, values in open_state.items():
            if values:
                raise ValueError(
                    f"successful session has unresolved {name}: {sorted(values)}"
                )

    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "session_id": root["session_id"],
        "events": state["count"],
        "terminal": terminal["type"],
        "audio_streams": len(streams),
        "latency_claims": latency_claims,
        "max_input_schedule_lag_ms": max_schedule_lag,
        "turn_events_resolved": len(resolved_turns),
        "utterances_final": len(final_utterances),
        "speaker_tracks": len(speaker_tracks),
        "identities_enrolled": len(enrolled_identities),
        "spoof_results": state["spoof_results"],
        "tool_calls_completed": len(closed_calls),
        "syntheses_completed": len(closed_synthesis),
        "syntheses_cancelled": len(cancelled_synthesis),
    }
