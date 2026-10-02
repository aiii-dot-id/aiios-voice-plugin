"""Fixed reference voices from the carrier-verified runtime, never UID enrollment.

Only the model-loading executor constructs this object. No operator-provided
path, download, microphone, enrollment or extra persistence is accepted here.
"""

from __future__ import annotations

import hashlib
import io
import re
import wave
from types import MappingProxyType

import numpy as np

from runtime.model_assets import checked, read_json


class ReferenceVoices:
    def __init__(self, catalog, mx):
        body, raw = read_json(catalog, max_bytes=65536)
        if (
            set(body) != {"schema", "default", "voices"}
            or body["schema"] != "aiii.voice.references"
        ):
            raise ValueError("reference voice catalog shape differs")
        rows = body["voices"]
        if type(rows) is not list or not 1 <= len(rows) <= 128:
            raise ValueError("bounded reference voice list required")
        labels, audio, evidence = {}, {}, []
        for row in rows:
            if type(row) is not dict or set(row) != {
                "id",
                "label",
                "file",
                "sha256",
                "source",
                "source_id",
            }:
                raise ValueError("reference voice fields differ")
            vid = row["id"]
            if (
                not isinstance(vid, str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", vid)
                or vid in labels
            ):
                raise ValueError("distinct stable reference voice IDs required")
            if any(
                not isinstance(row[k], str)
                or not row[k].strip()
                or len(row[k]) > 256
                or any(ord(c) < 32 for c in row[k])
                for k in ("label", "source", "source_id")
            ):
                raise ValueError("bounded voice label/provenance required")
            path = checked(catalog.parent, row["file"])
            if path.suffix != ".wav" or not 44 <= path.stat().st_size <= 1500000:
                raise ValueError("bounded PCM WAV reference required")
            with path.open("rb") as f:
                data = f.read(1500001)
            if len(data) > 1500000 or hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise ValueError("reference voice hash differs")
            with wave.open(io.BytesIO(data)) as f:
                if (
                    f.getnchannels(),
                    f.getsampwidth(),
                    f.getframerate(),
                    f.getcomptype(),
                ) != (1, 2, 24000, "NONE"):
                    raise ValueError("voice reference must be mono 24kHz PCM16")
                count = f.getnframes()
                if not 48000 <= count <= 720000:
                    raise ValueError(
                        "voice reference duration must be 2 through 30 seconds"
                    )
                pcm = f.readframes(count)
                if len(pcm) != count * 2:
                    raise ValueError("voice reference truncated")
            samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
            if float(np.max(np.abs(samples))) < 0.01:
                raise ValueError("silent reference voice refused")
            value = mx.array(samples)
            mx.eval(value)
            labels[vid], audio[vid] = row["label"], value
            evidence.append({**row, "samples": count, "sample_rate": 24000})
        if body["default"] not in labels:
            raise ValueError("default reference voice is not bound")
        self.default = body["default"]
        self.labels = MappingProxyType(labels)
        self._audio = MappingProxyType(audio)
        self.identity = {
            "catalog_sha256": hashlib.sha256(raw).hexdigest(),
            "default": self.default,
            "voices": evidence,
            "conditioning": "speaker_embedding_each_segment",
            "uid_enrollment": False,
        }

    def generation(self, voice):
        if voice not in self._audio:
            raise ValueError("reference voice not bound")
        # Speaker-only conditioning is the Base model's implemented path. No
        # transcript/ICL implies no hidden 1.5 repetition-penalty clamp and no
        # reference-content continuation. The same bound samples enter every
        # segment; these references never feed the UID enrollment owner.
        return {"ref_audio": self._audio[voice]}
