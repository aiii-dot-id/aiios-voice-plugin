import json
from pathlib import Path
from types import SimpleNamespace

from jsonschema import Draft202012Validator

from runtime.plugin_engine.speaker_event import host_observation


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = Draft202012Validator(json.loads(
    (ROOT / "spec/speaker_attribution.schema.json").read_text()
))
POLICY = SimpleNamespace(
    embedding_binding="a" * 64, fingerprint="b" * 64,
    threshold=0.55, minimum_margin=0.1,
)


def matched_result():
    return dict(
        attributes=7, outcome="known", embedding_binding=POLICY.embedding_binding,
        policy_sha256=POLICY.fingerprint, enrollment_revision=2,
        speaker_id="person-a", label="Named speaker", reason="accepted",
        start_sample=16000, end_sample=48000, sample_rate=16000,
        audio_sha256="c" * 64, score=0.8, margin=0.3,
        track_id="track-a", diarization_verified=True,
    )


def amendment(body):
    return dict(
        type="speaker_observation", session_id="session-a", sequence=8,
        id="session-a:8", observed_monotonic_ns=123456, **body,
    )


def test_verified_separated_match_has_host_filter_id_and_valid_schema():
    body = host_observation(matched_result(), POLICY)
    assert body["speaker_id"] == "person-a"
    assert body["decision"] == "known"
    assert body["used_for_permissions"] is False
    SCHEMA.validate(amendment(body))


def test_unseparated_match_cannot_grant_a_host_filter_id():
    result = matched_result()
    result["diarization_verified"] = False
    result.pop("track_id")
    body = host_observation(result, POLICY)
    assert body["decision"] == "uncertain"
    assert body["speaker_id"] == body["speaker"] == ""
    assert "speaker" not in body["evidence"]
    assert body["reason"] == "speaker_separation_unverified"
    SCHEMA.validate(amendment(body))
