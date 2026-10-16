"""Fixed VF104 physical controls, selected before acoustic outcomes are seen."""

import hashlib
import json

MANIFEST_SHA256 = "ea8673b4715927d283f30797fb20e451448e9ac2ed4f65a7cd2c1b6a26685b65"
IDS = (
    "84-121123-0018",
    "174-84280-0011",
    "251-137823-0007",
    "422-122949-0028",
    "652-129742-0008",
    "777-126732-0034",
)
SCHEDULE = (
    (1.0, "near", 1),
    (7.0, "far", 1),
    (14.0, "far", 2),
    (15.0, "near", 2),
    (22.0, "far", 3),
    (23.0, "near", 3),
    (30.0, "far", 4),
)
DURATION = 38.0


def source(panel, identifier):
    if identifier not in IDS:
        raise ValueError("utterance outside frozen short-speaker panel")
    path = panel / "manifest.json"
    if hashlib.sha256(path.read_bytes()).hexdigest() != MANIFEST_SHA256:
        raise ValueError("acoustic source manifest differs")
    rows = [r for r in json.loads(path.read_text())["samples"] if r["id"] == identifier]
    if len(rows) != 1:
        raise ValueError("missing or duplicate source utterance")
    row = rows[0]
    audio = panel / row["audio_file"]
    if not 0 < row["duration_seconds"] <= 4 or row["sample_rate"] != 16000:
        raise ValueError("unbounded acoustic source")
    if hashlib.sha256(audio.read_bytes()).hexdigest() != row["audio_sha256"]:
        raise ValueError("acoustic source audio differs")
    return audio, row


def interruptible(mode, enabled, epoch):
    # Sustained-overlap epoch 2 deliberately measures continuous double-talk.
    # Far-only/recovery epochs remain armed so false interruptions are visible.
    return (epoch in (1, 3, 4)) if mode == "panel" else enabled
