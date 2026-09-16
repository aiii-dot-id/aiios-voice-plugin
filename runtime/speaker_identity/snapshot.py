"""Data-only, bounded enrollment snapshots for host-mediated private storage.

This codec is not a file writer, enrollment grant, or second recognition path.
The caller owns serialized mutation and atomic durable host publication. A
failed/unknown publication must not expose the tentative working copy as current.
"""

import base64
import binascii
import json
from dataclasses import asdict

import numpy as np

from .identity import (
    DIMENSIONS,
    MAX_SAMPLES,
    MAX_SPEAKERS,
    IdentityStore,
    SpeakerIdentityError,
    canonical,
    sha_string,
    unit,
    valid_id,
)

MAX_SNAPSHOT_BYTES = 8 << 20
MAX_REVISION = (1 << 63) - 1


def _keys(value, wanted):
    if type(value) is not dict or set(value) != set(wanted):
        raise SpeakerIdentityError("invalid enrollment snapshot fields")


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise SpeakerIdentityError("duplicate enrollment snapshot key")
        value[key] = item
    return value


def load_snapshot(raw: bytes, policy) -> IdentityStore:
    """Validate everything before returning a new in-memory authority.

    Invalid/unreadable data is never interpreted as an empty enrollment set.
    Embeddings retain their exact little-endian float64 bytes, not rounded JSON
    numbers or another normalization. SQLite receives only fixed SQL + parameters.
    """
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_SNAPSHOT_BYTES:
        raise SpeakerIdentityError("enrollment snapshot byte limit or type")
    try:
        body = json.loads(raw, object_pairs_hook=_pairs)
        _keys(body, ("policy", "revision", "speakers"))
        if canonical(body).encode() != raw:
            raise SpeakerIdentityError("enrollment snapshot is not canonical")
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise SpeakerIdentityError("invalid enrollment snapshot JSON") from error
    if canonical(body["policy"]) != canonical(asdict(policy)):
        raise SpeakerIdentityError("enrollment policy/model binding differs")
    revision, speakers = body["revision"], body["speakers"]
    if type(revision) is not int or not 0 <= revision <= MAX_REVISION:
        raise SpeakerIdentityError("invalid enrollment revision")
    if type(speakers) is not list or len(speakers) > MAX_SPEAKERS:
        raise SpeakerIdentityError("invalid enrollment speaker count")
    prepared, ids, hashes = [], set(), set()
    for speaker in speakers:
        _keys(speaker, ("id", "label", "samples"))
        sid, label, samples = speaker["id"], speaker["label"], speaker["samples"]
        valid_id(sid)
        if sid in ids:
            raise SpeakerIdentityError("duplicate enrollment speaker")
        ids.add(sid)
        if (
            type(label) is not str
            or not 1 <= len(label) <= 128
            or any(ord(c) < 32 for c in label)
        ):
            raise SpeakerIdentityError("invalid enrollment label")
        if type(samples) is not list or not 1 <= len(samples) <= MAX_SAMPLES:
            raise SpeakerIdentityError("invalid enrollment sample count")
        recordings, vectors = [], []
        for sample in samples:
            _keys(sample, ("audio_sha256", "embedding_f64le_b64"))
            audio_sha = sha_string(sample["audio_sha256"])
            if audio_sha in hashes:
                raise SpeakerIdentityError("duplicate enrollment audio")
            hashes.add(audio_sha)
            encoded = sample["embedding_f64le_b64"]
            if type(encoded) is not str or len(encoded) != 2732:
                raise SpeakerIdentityError("invalid enrollment embedding encoding")
            try:
                blob = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as error:
                raise SpeakerIdentityError(
                    "invalid enrollment embedding encoding"
                ) from error
            if (
                len(blob) != DIMENSIONS * 8
                or base64.b64encode(blob).decode() != encoded
            ):
                raise SpeakerIdentityError("invalid enrollment embedding bytes")
            vector = np.frombuffer(blob, dtype="<f8")
            if (
                not np.isfinite(vector).all()
                or abs(float(np.linalg.norm(vector)) - 1) > 1e-6
            ):
                raise SpeakerIdentityError("invalid enrollment unit vector")
            vectors.append(vector)
            recordings.append((sid, audio_sha, blob))
        unit(np.sum(vectors, axis=0))  # Refuse a zero/nonfinite speaker centroid.
        if [r[1] for r in recordings] != sorted(r[1] for r in recordings):
            raise SpeakerIdentityError("enrollment samples are not ordered")
        prepared.append((sid, label, recordings))
    if [row[0] for row in prepared] != sorted(ids):
        raise SpeakerIdentityError("enrollment speakers are not ordered")
    if speakers and revision == 0:
        raise SpeakerIdentityError("enrolled speakers require a positive revision")
    store = IdentityStore.memory(policy)
    try:
        with store.db:
            store.db.execute("BEGIN IMMEDIATE")
            for sid, label, recordings in prepared:
                store.db.execute("INSERT INTO speakers VALUES (?,?)", (sid, label))
                store.db.executemany("INSERT INTO samples VALUES (?,?,?)", recordings)
            store.db.execute("UPDATE authority SET revision=?", (revision,))
        store.list()  # Reuse the actual recognition-store integrity checks too.
    except Exception:
        store.close()
        raise
    return store


def dump_snapshot(store: IdentityStore) -> bytes:
    """Read one transaction; validate the full round-trip before returning bytes."""
    with store.db:
        store.db.execute("BEGIN")
        revision = store._revision()
        if store.db.execute("PRAGMA foreign_key_check").fetchone():
            raise SpeakerIdentityError("corrupt enrollment references")
        rows = store.db.execute(
            "SELECT CASE WHEN length(id)<=96 THEN id END,CASE WHEN length(label)<=128 THEN label END FROM speakers ORDER BY id LIMIT ?",
            (MAX_SPEAKERS + 1,),
        ).fetchall()
        speakers = []
        for sid, label in rows:
            samples = store.db.execute(
                "SELECT CASE WHEN length(audio_sha)=64 THEN audio_sha END,CASE WHEN length(embedding)=2048 THEN embedding END FROM samples WHERE speaker_id=? ORDER BY audio_sha LIMIT ?",
                (sid, MAX_SAMPLES + 1),
            ).fetchall()
            if any(type(blob) is not bytes for _, blob in samples):
                raise SpeakerIdentityError("corrupt enrollment embedding size/type")
            speakers.append(
                {
                    "id": sid,
                    "label": label,
                    "samples": [
                        {
                            "audio_sha256": audio_sha,
                            "embedding_f64le_b64": base64.b64encode(blob).decode(),
                        }
                        for audio_sha, blob in samples
                    ],
                }
            )
    raw = canonical(
        {"policy": asdict(store.policy), "revision": revision, "speakers": speakers}
    ).encode()
    with load_snapshot(raw, store.policy):
        pass
    return raw
