"""Score a retained physical recording, with source-TTS recognition as control.

No microphone opens here. Recognition remains in the pinned CUDA worker and
each terminal is immediately followed by start/close to exercise reuse.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import numpy as np


def word_error(reference, hypothesis):
    words = lambda text: re.findall(r"[a-z0-9]+", text.casefold())
    expected, actual = words(reference), words(hypothesis)
    previous = list(range(len(actual) + 1))
    for i, wanted in enumerate(expected, 1):
        current = [i]
        for j, observed in enumerate(actual, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (wanted != observed),
                )
            )
        previous = current
    return {
        "errors": previous[-1],
        "reference_words": len(expected),
        "wer": previous[-1] / len(expected),
    }


def main():
    import soundfile as sf
    from scipy.signal import resample_poly

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--stt-root", type=Path, required=True)
    parser.add_argument("--physical", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture-gain", type=float, default=1.0)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    physical = json.loads((args.physical / "result.json").read_text())
    if (
        physical["status"] != "captured_for_acoustic_evaluation"
        or not physical["restoration_verified"]
    ):
        raise ValueError("physical capture/restoration gate missing")
    reference = (
        args.root.parent
        / "vf101-cuda-panel/cached-r1/items/interruption-recovery-03/streamed.wav"
    )
    if digest(reference) != physical["input_audio_sha256"]:
        raise ValueError("rendered source identity differs")
    source, rate = sf.read(reference, dtype="float32")
    if rate != 24000:
        raise ValueError("reference rate differs")
    microphone = args.physical / "microphone-0.wav"
    if (
        digest(microphone)
        != physical["recordings"]["alsa_input.3.analog-stereo"]["sha256"]
    ):
        raise ValueError("microphone identity differs")
    acoustic, rate = sf.read(microphone, dtype="float32")
    if rate != 16000:
        raise ValueError("capture rate differs")
    if not np.isfinite(args.capture_gain) or not 0.1 <= args.capture_gain <= 10:
        raise ValueError("bounded diagnostic gain required")
    acoustic = acoustic * args.capture_gain
    if np.max(np.abs(acoustic)) >= 0.99:
        raise ValueError("diagnostic gain would clip")
    expected = "Continuing from the interruption: the first preserved word is recovery, and the sentence remains complete."
    report = {
        "status": "running",
        "expected": expected,
        "scope": "one synthetic-voice acoustic roundtrip; not human quality or interruption qualification",
        "items": [],
        "source_sha256": digest(reference),
        "physical_report_sha256": digest(args.physical / "result.json"),
        "microphone_sha256": digest(microphone),
        "capture_gain": args.capture_gain,
    }
    frozen = args.output / "executed-source"
    frozen.mkdir()
    report["sources"] = {}
    for path in (
        Path(__file__),
        args.root / "runtime/stt/cuda_resident.py",
        args.stt_root / "cuda_stream.py",
    ):
        report["sources"][path.name] = digest(path)
        shutil.copy2(path, frozen / path.name)
    events = queue.Queue()
    messages = []
    child = subprocess.Popen(
        [
            str(args.stt_root / ".venv/bin/python"),
            str(args.root / "runtime/stt/cuda_resident.py"),
            "--root",
            str(args.stt_root),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    def receive():
        with (args.output / "worker.log").open("x") as log:
            for line in child.stdout:
                log.write(line)
                log.flush()
                if line.startswith("VF102 "):
                    row = json.loads(line[6:])
                    messages.append(row)
                    events.put(row)
            events.put({"event": "exited"})

    reader = threading.Thread(target=receive, daemon=True)
    reader.start()

    def send(op, **fields):
        child.stdin.write(json.dumps({"op": op, **fields}) + "\n")
        child.stdin.flush()

    try:
        ready = events.get(timeout=60)
        if ready["event"] != "ready":
            raise RuntimeError(ready)
        report["ready"] = ready
        for name, pcm in (
            ("source_control", resample_poly(source, 2, 3).astype(np.float32)),
            ("physical_c920", acoustic),
        ):
            if (
                pcm.ndim != 1
                or not np.isfinite(pcm).all()
                or not 0 < len(pcm) <= 16000 * 12
            ):
                raise ValueError("invalid bounded PCM")
            admitted_path = args.output / f"admitted-{name}.wav"
            sf.write(admitted_path, pcm, 16000, subtype="FLOAT")
            send("start", sid=name)
            for start in range(0, len(pcm), 512):
                send(
                    "pcm",
                    sid=name,
                    start=start,
                    pcm=base64.b64encode(
                        pcm[start : start + 512].astype("<f4").tobytes()
                    ).decode(),
                )
                time.sleep(0.032)
            send("finish", sid=name)
            deadline = time.monotonic() + 20
            while True:
                row = events.get(timeout=max(0.001, deadline - time.monotonic()))
                if row["event"] in ("error", "cancelled", "exited"):
                    raise RuntimeError(row)
                if row["event"] == "final" and row["sid"] == name:
                    break
            report["items"].append(
                {
                    "name": name,
                    "final": row,
                    "score": word_error(expected, row["text"]),
                    "samples": len(pcm),
                    "rms": float(np.sqrt(np.mean(pcm**2))),
                    "admitted_wav_sha256": digest(admitted_path),
                }
            )
        send("close")
        if child.wait(timeout=10) != 0:
            raise RuntimeError("worker close failed")
        report["status"] = (
            "pass"
            if all(item["score"]["errors"] == 0 for item in report["items"])
            else "recognition_mismatch"
        )
    except Exception as exc:  # noqa: BLE001 - retain acoustic qualification failures
        report.update(status="failed", error=repr(exc))
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
        reader.join(3)
        report["events"] = messages
        (args.output / "result.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({key: value for key, value in report.items() if key != "events"}))
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
