"""Bounded recognition decisions and transactional enrollment, without inference."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

DIMENSIONS = 256
MAX_SPEAKERS = 256
MAX_SAMPLES = 8


class SpeakerIdentityError(ValueError):
    pass


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def sha_string(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise SpeakerIdentityError("expected a lowercase SHA-256 identity")
    return value


def unit(value) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if array.shape != (DIMENSIONS,) or not np.isfinite(array).all():
        raise SpeakerIdentityError("embedding must contain 256 finite values")
    norm = float(np.linalg.norm(array))
    if not math.isfinite(norm) or norm < 1e-12:
        raise SpeakerIdentityError("embedding has invalid norm")
    return array / norm


@dataclass(frozen=True)
class Policy:
    embedding_binding: str
    threshold: float
    minimum_margin: float
    calibration_sha256: str
    minimum_enrollment_samples: int = 1

    def __post_init__(self):
        sha_string(self.embedding_binding)
        sha_string(self.calibration_sha256)
        if (
            type(self.minimum_enrollment_samples) is not int
            or not 1 <= self.minimum_enrollment_samples <= MAX_SAMPLES
        ):
            raise SpeakerIdentityError("invalid minimum enrollment sample count")
        for name, low, high in (("threshold", -1, 1), ("minimum_margin", 0, 2)):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not math.isfinite(value)
                or not low <= value <= high
            ):
                raise SpeakerIdentityError(f"invalid {name}")

    @property
    def fingerprint(self):
        return digest(asdict(self))


@dataclass(frozen=True)
class Decision:
    outcome: str
    speaker_id: str | None
    label: str | None
    score: float | None
    margin: float | None
    reason: str
    enrollment_revision: int
    policy_sha256: str


def valid_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", value
    ):
        raise SpeakerIdentityError(
            "speaker id must be 1..96 simple identifier characters"
        )
    return value


class IdentityStore:
    """One SQLite authority, explicit creation, exact policy binding, no audio stored.

    Each instance belongs to one thread. Independent instances/processes serialize
    enrollment transactions through SQLite. Inference runs before the transaction;
    identify reads one consistent enrollment snapshot and reports its revision.
    """

    @staticmethod
    def _initialize(connection, policy):
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE authority (singleton INTEGER PRIMARY KEY CHECK(singleton=1), policy TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>=0))"
            )
            connection.execute(
                "CREATE TABLE speakers (id TEXT PRIMARY KEY, label TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE samples (speaker_id TEXT NOT NULL REFERENCES speakers(id) ON DELETE CASCADE, audio_sha TEXT NOT NULL UNIQUE, embedding BLOB NOT NULL CHECK(length(embedding)=2048), PRIMARY KEY(speaker_id,audio_sha))"
            )
            connection.execute(
                "INSERT INTO authority VALUES (1,?,0)", (canonical(asdict(policy)),)
            )

    @classmethod
    def memory(cls, policy: Policy):
        """Explicit, thread-confined working copy; grants no persistence authority.

        Reuses the exact enrollment transactions and decision path. No temporary
        file, database deserialization, schema from a caller or model load.
        """
        self = cls.__new__(cls)
        self.path, self.policy = None, policy
        self.db = sqlite3.connect(":memory:")
        try:
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA temp_store=MEMORY")
            self.db.execute("PRAGMA trusted_schema=OFF")
            self.db.execute("PRAGMA secure_delete=ON")
            cls._initialize(self.db, policy)
        except Exception:
            self.db.close()
            raise
        return self

    @classmethod
    def create(cls, path: Path, policy: Policy):
        path = Path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Do not silently replace or initialize an existing, damaged, or wrong DB.
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        connection = sqlite3.connect(path)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            cls._initialize(connection, policy)
        finally:
            connection.close()
        return cls(path, policy)

    def __init__(self, path: Path, policy: Policy):
        self.path, self.policy = Path(path), policy
        self.db = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=rw", uri=True, timeout=2
        )
        try:
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA secure_delete=ON")
            self._revision()
        except Exception:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _revision(self):
        rows = self.db.execute(
            "SELECT policy,revision FROM authority WHERE singleton=1"
        ).fetchall()
        if len(rows) != 1 or rows[0][0] != canonical(asdict(self.policy)):
            raise SpeakerIdentityError(
                "enrollment policy/model binding differs or is missing"
            )
        revision = rows[0][1]
        if type(revision) is not int or revision < 0:
            raise SpeakerIdentityError("invalid enrollment revision")
        return revision

    def _advance(self):
        self.db.execute("UPDATE authority SET revision=revision+1 WHERE singleton=1")
        return self._revision()

    def enroll(
        self,
        speaker_id: str,
        label: str,
        embedding,
        *,
        audio_sha256: str,
        embedding_binding: str,
    ):
        return self.enroll_many(
            speaker_id,
            label,
            [(audio_sha256, embedding)],
            embedding_binding=embedding_binding,
        )

    def enroll_many(self, speaker_id, label, recordings, *, embedding_binding):
        """Commit an explicitly selected recording set in one transaction.

        Inference is already complete. This reuses the single-sample authority
        and advances its revision once; no partial batch becomes visible.
        """
        valid_id(speaker_id)
        if (
            not isinstance(label, str)
            or not 1 <= len(label) <= 128
            or any(ord(c) < 32 for c in label)
        ):
            raise SpeakerIdentityError("label must be 1..128 printable characters")
        if embedding_binding != self.policy.embedding_binding:
            raise SpeakerIdentityError("embedding model/frontend binding differs")
        if (
            not isinstance(recordings, (list, tuple))
            or not 1 <= len(recordings) <= MAX_SAMPLES
        ):
            raise SpeakerIdentityError("bounded enrollment recordings required")
        prepared = [
            (sha_string(audio_sha), unit(vector)) for audio_sha, vector in recordings
        ]
        if len({audio_sha for audio_sha, _ in prepared}) != len(prepared):
            raise SpeakerIdentityError("duplicate evidence refused within enrollment")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self._revision()
            if any(
                self.db.execute(
                    "SELECT 1 FROM samples WHERE audio_sha=?", (audio_sha,)
                ).fetchone()
                for audio_sha, _ in prepared
            ):
                raise SpeakerIdentityError(
                    "this audio is already enrolled; duplicate evidence refused"
                )
            existing = self.db.execute(
                "SELECT label FROM speakers WHERE id=?", (speaker_id,)
            ).fetchone()
            if existing and existing[0] != label:
                raise SpeakerIdentityError(
                    "existing speaker label differs; no silent identity replacement"
                )
            count = self.db.execute(
                "SELECT COUNT(*) FROM samples WHERE speaker_id=?", (speaker_id,)
            ).fetchone()[0]
            if count + len(prepared) > MAX_SAMPLES:
                raise SpeakerIdentityError("speaker enrollment sample limit reached")
            # A successful enrollment must leave a usable template, not plant a
            # zero/nonfinite centroid that breaks every later recognition call.
            prior = self.db.execute(
                "SELECT embedding FROM samples WHERE speaker_id=?", (speaker_id,)
            ).fetchall()
            unit(
                np.sum(
                    [unit(np.frombuffer(r[0], dtype="<f8")) for r in prior]
                    + [vector for _, vector in prepared],
                    axis=0,
                )
            )
            if not existing:
                if (
                    self.db.execute("SELECT COUNT(*) FROM speakers").fetchone()[0]
                    >= MAX_SPEAKERS
                ):
                    raise SpeakerIdentityError("speaker limit reached")
                self.db.execute(
                    "INSERT INTO speakers VALUES (?,?)", (speaker_id, label)
                )
            self.db.executemany(
                "INSERT INTO samples VALUES (?,?,?)",
                [
                    (speaker_id, audio_sha, vector.astype("<f8").tobytes())
                    for audio_sha, vector in prepared
                ],
            )
            revision = self._advance()
        return {
            "operation": "enrolled",
            "speaker_id": speaker_id,
            "samples": count + len(prepared),
            "state": "ready"
            if count + len(prepared) >= self.policy.minimum_enrollment_samples
            else "collecting",
            "revision": revision,
        }

    def _snapshot(self):
        # BEGIN is necessary: separate SELECTs must not see different commits.
        with self.db:
            self.db.execute("BEGIN")
            revision = self._revision()
            if self.db.execute("PRAGMA foreign_key_check").fetchone():
                raise SpeakerIdentityError("corrupt enrollment references")
            rows = self.db.execute(
                "SELECT p.id,p.label,s.audio_sha,s.embedding FROM speakers p LEFT JOIN samples s ON s.speaker_id=p.id ORDER BY p.id,s.audio_sha LIMIT ?",
                (MAX_SPEAKERS * MAX_SAMPLES + 1,),
            ).fetchall()
        groups = {}
        for sid, label, audio_sha, blob in rows:
            valid_id(sid)
            if (
                not isinstance(label, str)
                or not 1 <= len(label) <= 128
                or any(ord(c) < 32 for c in label)
            ):
                raise SpeakerIdentityError("corrupt enrollment label")
            sha_string(audio_sha)
            if not isinstance(blob, bytes) or len(blob) != DIMENSIONS * 8:
                raise SpeakerIdentityError("corrupt enrollment embedding")
            raw = np.frombuffer(blob, dtype="<f8")
            if not np.isfinite(raw).all() or abs(float(np.linalg.norm(raw)) - 1) > 1e-6:
                raise SpeakerIdentityError("corrupt enrollment unit vector")
            item = groups.setdefault(sid, {"label": label, "vectors": []})
            item["vectors"].append(raw)
        if len(groups) > MAX_SPEAKERS or any(
            not 1 <= len(g["vectors"]) <= MAX_SAMPLES for g in groups.values()
        ):
            raise SpeakerIdentityError("corrupt enrollment cardinality")
        return revision, groups

    def list(self):
        revision, groups = self._snapshot()
        return {
            "revision": revision,
            "speakers": [
                {"speaker_id": sid, "label": g["label"], "samples": len(g["vectors"])}
                for sid, g in groups.items()
            ],
        }

    def identify(self, embedding, *, embedding_binding: str) -> Decision:
        if embedding_binding != self.policy.embedding_binding:
            raise SpeakerIdentityError("embedding model/frontend binding differs")
        query = unit(embedding)
        revision, groups = self._snapshot()
        common = {
            "enrollment_revision": revision,
            "policy_sha256": self.policy.fingerprint,
        }
        if not groups:
            return Decision(
                "unknown", None, None, None, None, "no_enrollments", **common
            )
        scores = sorted(
            (
                (
                    float(np.clip(query @ unit(np.mean(g["vectors"], axis=0)), -1, 1)),
                    sid,
                )
                for sid, g in groups.items()
            ),
            key=lambda row: (-row[0], row[1]),
        )
        score, sid = scores[0]
        margin = score - scores[1][0] if len(scores) > 1 else None
        if len(groups[sid]["vectors"]) < self.policy.minimum_enrollment_samples:
            return Decision(
                "unknown",
                None,
                None,
                score,
                margin,
                "insufficient_enrollment",
                **common,
            )
        if score < self.policy.threshold:
            return Decision(
                "unknown",
                None,
                None,
                score,
                margin,
                "below_acceptance_threshold",
                **common,
            )
        # A tied nearest neighbour is never an identity, even with margin=0.
        if margin is not None and (margin <= 0 or margin < self.policy.minimum_margin):
            return Decision(
                "ambiguous",
                None,
                None,
                score,
                margin,
                "insufficient_separation",
                **common,
            )
        return Decision(
            "known", sid, groups[sid]["label"], score, margin, "accepted", **common
        )

    def remove(self, speaker_id: str):
        valid_id(speaker_id)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            revision = self._revision()
            removed = self.db.execute(
                "DELETE FROM speakers WHERE id=?", (speaker_id,)
            ).rowcount
            if removed:
                revision = self._advance()
        return {
            "operation": "removed",
            "speaker_id": speaker_id,
            "removed": bool(removed),
            "revision": revision,
        }

    def reset(self):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self._revision()
            count = self.db.execute("SELECT COUNT(*) FROM speakers").fetchone()[0]
            self.db.execute("DELETE FROM speakers")
            revision = self._advance()
        return {"operation": "reset", "removed_speakers": count, "revision": revision}
