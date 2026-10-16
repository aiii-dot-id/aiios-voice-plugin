"""Create a fixed engineering panel from hash-bound public recordings, not TTS."""
import argparse
from array import array
import hashlib
import json
import math
from pathlib import Path
import sys
import wave

IDS = ("237-134500-0032", "672-122797-0069")
PANEL_SHA = "e0b51eac7c7121d0f38b96fcf9e1948cdb60493a9c60ad498e6557382d5bedd6"
RATE = 16000


def digest(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def read_audio(path, expected):
    if digest(path) != expected:
        raise ValueError("source audio changed")
    with wave.open(str(path), "rb") as f:
        if (f.getframerate(), f.getnchannels(), f.getsampwidth()) != (RATE, 1, 2):
            raise ValueError("expected 16kHz mono PCM16")
        values = array("h", f.readframes(f.getnframes()))
    if sys.byteorder != "little":
        values.byteswap()
    rms = math.sqrt(sum((v / 32768) ** 2 for v in values) / len(values))
    if rms < 1e-6:
        raise ValueError("silent source")
    return [v / 32768 * .08 / rms for v in values]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-panel", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    manifest = a.source_panel / "manifest.json"
    if digest(manifest) != PANEL_SHA:
        raise ValueError("source panel changed")
    rows = {x["id"]: x for x in json.loads(manifest.read_text())["samples"]}
    selected = [rows[i] for i in IDS]
    audio = [read_audio(a.source_panel / "audio" / r["audio_file"], r["audio_sha256"])
             for r in selected]
    # Starts and file extents are mixing geometry, NOT speech/word alignments.
    layouts = {
        "solo_a": [(0, 1., 1.)],
        "solo_b": [(1, 1., 1.)],
        "alternating": [(0, 1., 1.), (1, 7., 1.), (0, 13., 1.)],
        "overlap_equal": [(0, 1., 1.), (1, 7., 1.), (0, 13., 1.), (1, 14., 1.)],
        "overlap_b_quiet": [(0, 1., 1.), (1, 7., 1.), (0, 13., 1.), (1, 14., .25)],
        "overlap_a_quiet": [(0, 1., 1.), (1, 7., 1.), (0, 13., .25), (1, 14., 1.)],
        "overlap_cold": [(0, 1., 1.), (1, 2., 1.)],
    }
    a.output.mkdir(parents=True, exist_ok=False)
    result = {"schema": 1, "source_manifest_sha256": PANEL_SHA,
              "scope": "public read-speech engineering panel; no natural conversation or identity accuracy claim",
              "selection": "two preselected recordings; no candidate outputs inspected",
              "gate": {"max_case_cpwer": .25, "max_speaker_wer": .35,
                       "required_speakers": "all reference speakers in each case"},
              "sources": selected, "cases": []}
    for name, placements in layouts.items():
        n = max(round(t * RATE) + len(audio[i]) for i, t, _ in placements) + RATE
        mixture = [0.] * n
        refs = []
        for i, t, gain in placements:
            start = round(t * RATE)
            for j, sample in enumerate(audio[i]):
                mixture[start + j] += sample * gain
            refs.append({"session_id": name, "speaker": str(selected[i]["speaker_id"]),
                         "start_time": start / RATE,
                         "end_time": (start + len(audio[i])) / RATE,
                         "words": selected[i]["reference_text"], "gain": gain})
        peak = max(abs(v) for v in mixture)
        scale = min(1., .95 / peak)
        samples = array("h", (round(v * scale * 32767) for v in mixture))
        if sys.byteorder != "little":
            samples.byteswap()
        path = a.output / (name + ".wav")
        with wave.open(str(path), "wb") as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(RATE)
            f.writeframes(samples.tobytes())
        result["cases"].append({"id": name, "audio_file": path.name,
                                "audio_sha256": digest(path), "samples": n,
                                "reference": refs, "peak_rescale": scale})
    (a.output / "panel.json").write_text(json.dumps(result, indent=2) + "\n")
    # This is the ONLY input metadata passed to the model: no text, speaker
    # labels, enrollment, reference intervals or expected speaker count.
    with (a.output / "inference.jsonl").open("x") as f:
        for case in result["cases"]:
            f.write(json.dumps({"audio_filepath": str((a.output / case["audio_file"]).resolve())}) + "\n")
    print(json.dumps({"cases": len(layouts), "panel_sha256": digest(a.output / "panel.json")}))


if __name__ == "__main__":
    main()
