"""Deterministic Voice Core evidence replay and backend conformance."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

from .protocol import validate_trace
from .timing import sample_boundary_monotonic_ns

SCHEMA = "aiii.voice.core.replay-bundle"
BYTE_EVENTS = {"input_audio", "playback_reference", "audio_chunk"}
SAMPLE_BYTES = {"pcm_s16le": 2, "pcm_f32le": 4}


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _exact(value: dict[str, Any], required: set[str], label: str) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required)
    if missing:
        raise ValueError(f"{label} lacks required fields: {missing}")
    if unknown:
        raise ValueError(f"{label} has unknown fields: {unknown}")


def _sha256(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _decode_object(raw: Any, index: int) -> tuple[str, bytes]:
    label = f"objects[{index}]"
    if not isinstance(raw, dict):
        raise TypeError(f"{label} must be an object")
    _exact(raw, {"base64", "bytes", "content_sha256"}, label)
    digest = _sha256(raw["content_sha256"], f"{label}.content_sha256")
    if isinstance(raw["bytes"], bool) or not isinstance(raw["bytes"], int):
        raise TypeError(f"{label}.bytes must be a non-negative integer")
    if raw["bytes"] < 0:
        raise ValueError(f"{label}.bytes must be a non-negative integer")
    if not isinstance(raw["base64"], str):
        raise TypeError(f"{label}.base64 must be a string")
    try:
        content = base64.b64decode(raw["base64"], validate=True)
    except ValueError as error:
        raise ValueError(f"{label}.base64 is invalid") from error
    if base64.b64encode(content).decode("ascii") != raw["base64"]:
        raise ValueError(f"{label}.base64 is not canonical")
    if len(content) != raw["bytes"]:
        raise ValueError(f"{label}.bytes does not match decoded content")
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError(f"{label}.content_sha256 does not match decoded content")
    return digest, content


@dataclass(frozen=True)
class ReplayRecord:
    event: dict[str, Any]
    content: bytes | None
    sample_boundary_offset_ns: int | None

    def receipt_value(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "content_sha256": (
                hashlib.sha256(self.content).hexdigest()
                if self.content is not None
                else None
            ),
            "sample_boundary_offset_ns": self.sample_boundary_offset_ns,
        }


class ReplayBackend(Protocol):
    def start(self, trace: dict[str, Any]) -> None: ...

    def reset(self, scope: str) -> None: ...

    def consume(self, record: ReplayRecord) -> None: ...

    def finish(self, terminal: str) -> dict[str, Any]: ...


def _receipt(records: list[ReplayRecord], *, terminal: str, resets: int) -> dict[str, Any]:
    digest = hashlib.sha256()
    for record in records:
        encoded = canonical_json(record.receipt_value())
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return {
        "schema": "aiii.voice.core.backend-receipt",
        "schema_version": 1,
        "events_consumed": len(records),
        "resets": resets,
        "terminal": terminal,
        "consumption_sha256": digest.hexdigest(),
    }


class DigestReplayBackend:
    """Minimal conforming backend that receipts every replayed observation."""

    def __init__(self) -> None:
        self._records: list[ReplayRecord] = []
        self._resets = 0
        self._started = False

    def start(self, trace: dict[str, Any]) -> None:
        if self._started:
            raise RuntimeError("backend already started")
        self._started = True

    def reset(self, scope: str) -> None:
        if not self._started or scope != "session":
            raise RuntimeError("backend received an invalid reset")
        self._resets += 1

    def consume(self, record: ReplayRecord) -> None:
        if not self._started:
            raise RuntimeError("backend was not started")
        self._records.append(record)

    def finish(self, terminal: str) -> dict[str, Any]:
        if not self._started:
            raise RuntimeError("backend was not started")
        return _receipt(self._records, terminal=terminal, resets=self._resets)


def _build_records(
    trace: dict[str, Any], objects: dict[str, bytes]
) -> list[ReplayRecord]:
    streams = {stream["stream_id"]: stream for stream in trace["audio_streams"]}
    referenced: set[str] = set()
    records: list[ReplayRecord] = []
    for event in trace["events"]:
        content: bytes | None = None
        boundary: int | None = None
        if event["type"] in BYTE_EVENTS:
            digest = event["content_sha256"]
            if digest not in objects:
                raise ValueError(
                    f"event {event['event_id']} references a missing content object"
                )
            referenced.add(digest)
            content = objects[digest]
            stream = streams[event["stream_id"]]
            samples = event["end_sample"] - event["start_sample"]
            expected_bytes = (
                samples * stream["channels"] * SAMPLE_BYTES[stream["sample_type"]]
            )
            if len(content) != expected_bytes:
                raise ValueError(
                    f"event {event['event_id']} content has {len(content)} bytes; "
                    f"expected {expected_bytes}"
                )
            if stream["direction"] == "input":
                boundary = sample_boundary_monotonic_ns(
                    origin_monotonic_ns=1,
                    end_sample=event["end_sample"],
                    sample_rate_hz=stream["sample_rate_hz"],
                ) - 1
        records.append(
            ReplayRecord(
                event=event,
                content=content,
                sample_boundary_offset_ns=boundary,
            )
        )
    unused = sorted(set(objects) - referenced)
    if unused:
        raise ValueError(f"replay bundle contains unreferenced content objects: {unused}")
    return records


def replay_bundle(bundle: Any, backend: ReplayBackend) -> dict[str, Any]:
    """Replay one exact evidence bundle without pacing or latency claims."""
    if not isinstance(bundle, dict):
        raise TypeError("replay bundle must be an object")
    _exact(
        bundle,
        {"schema", "schema_version", "trace", "trace_sha256", "objects"},
        "replay bundle",
    )
    if bundle["schema"] != SCHEMA or bundle["schema_version"] != 1:
        raise ValueError("replay bundle schema or version is unsupported")
    trace = bundle["trace"]
    if not isinstance(trace, dict):
        raise TypeError("replay bundle trace must be an object")
    expected_trace_sha256 = _sha256(bundle["trace_sha256"], "trace_sha256")
    actual_trace_sha256 = hashlib.sha256(canonical_json(trace)).hexdigest()
    if actual_trace_sha256 != expected_trace_sha256:
        raise ValueError("trace_sha256 does not match canonical trace bytes")
    summary = validate_trace(trace)
    if trace["timing"]["mode"] != "replay" or summary["latency_claims"]:
        raise ValueError("deterministic replay requires replay timing without latency claims")
    raw_objects = bundle["objects"]
    if not isinstance(raw_objects, list):
        raise TypeError("replay bundle objects must be an array")
    objects: dict[str, bytes] = {}
    for index, raw in enumerate(raw_objects):
        digest, content = _decode_object(raw, index)
        if digest in objects:
            raise ValueError(f"duplicate replay content object: {digest}")
        objects[digest] = content
    records = _build_records(trace, objects)
    resets = sum(record.event["type"] == "state_reset" for record in records)
    terminal = summary["terminal"]
    expected_receipt = _receipt(records, terminal=terminal, resets=resets)
    try:
        backend.start(trace)
        for record in records:
            if record.event["type"] == "state_reset":
                backend.reset(record.event["scope"])
            backend.consume(record)
        actual_receipt = backend.finish(terminal)
    except Exception as error:
        raise RuntimeError("Voice Core backend failed during deterministic replay") from error
    if actual_receipt != expected_receipt:
        raise ValueError("backend receipt does not match the exact replayed observations")
    return {
        "schema": SCHEMA,
        "trace_sha256": actual_trace_sha256,
        "events": len(records),
        "objects": len(objects),
        "terminal": terminal,
        "backend_receipt": actual_receipt,
        "latency_claims": False,
    }
