"""Measured echo-window/VAD evidence plus fixed CUDA transcription windows."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

from scripts.probe_linux_acoustic_stt import word_error


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def window_metrics(rows, start, end, key):
    if key not in ("clean", "raw") or not 0 <= start < end:
        raise ValueError("invalid evidence window")
    position = "end_sample" if key == "clean" else "raw_end_sample"
    selected = [r for r in rows if key in r and start < int(r[position]) <= end]
    if not selected:
        raise ValueError("empty evidence window")
    if any(not np.isfinite(r[key]) or not 0 <= r[key] <= 1 for r in selected):
        raise ValueError("invalid VAD probability")
    active = [r for r in selected if r[key] >= 0.5]
    return {
        "frames": len(selected),
        "speech_frames": len(active),
        "maximum": max(r[key] for r in selected),
        "first_speech_sample": active[0][position] if active else None,
    }


def output_pass(capture, expected_samples):
    """Epoch counters reset: check every epoch, not only the final snapshot."""
    events = capture["events"]
    if any(e["event"] == "underflow_observed" for e in events):
        return False
    if any(s["underflows"] or s["failed"] for s in capture["native_states"]):
        return False
    ends = [e for e in events if e["event"] == "far_end"]
    interrupted = capture.get("interrupt", False)
    if [e["number"] for e in ends] != ([1, 3] if interrupted else [1, 2]):
        return False
    if any(e["underflows"] or e["written"] != expected_samples for e in ends):
        return False
    stops = [e for e in events if e["event"] == "vad_stop"]
    acks = [e for e in events if e["event"] == "stop_ack_observed"]
    if not interrupted:
        return not stops and not acks
    return (
        len(stops) == len(acks) == 1
        and stops[0]["number"] == acks[0]["number"] == 2
        and stops[0]["at_ns"] < acks[0]["at_ns"]
        and not stops[0]["native_before"]["underflows"]
        and acks[0]["native"]["state"] == 0
        and acks[0]["native"]["discarded"] > 0
    )


def main():
    import soundfile as sf

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    capture = json.loads((args.capture / "result.json").read_text())
    if (
        capture["status"] != "captured_for_echo_evaluation"
        or not capture["restoration_verified"]
    ):
        raise ValueError("capture/restoration failed")
    audio = {}
    for name in ("microphone", "reference", "clean"):
        path = args.capture / (name + ".wav")
        if sha(path) != capture["recordings"][name]["sha256"]:
            raise ValueError("recording hash differs")
        values, rate = sf.read(path, dtype="float32")
        if rate != 16000 or values.ndim != 1 or not np.isfinite(values).all():
            raise ValueError("recording PCM invalid")
        audio[name] = values
    if len(audio["clean"]) != len(audio["microphone"]):
        raise ValueError("AEC tail lost")

    def anchor(event, number):
        return next(
            e["capture_samples"]
            for e in capture["events"]
            if e["event"] == event and e.get("number") == number
        )

    windows = {}
    for number, name in ((1, "near_only"), (2, "double_talk")):
        a = max(0, anchor("near_start", number) - 4096)
        b = min(len(audio["microphone"]), anchor("near_end", number) + 10240)
        windows[name] = (a, b)
    windows["far_only"] = (anchor("far_start", 1), anchor("near_start", 2))
    windows["far_cold_first_second"] = (
        anchor("far_start", 1),
        anchor("far_start", 1) + 16000,
    )
    windows["far_established"] = (
        anchor("far_start", 1) + 16000,
        anchor("near_start", 2),
    )
    report = {
        "status": "running",
        "scope": "two loudspeaker replay, not spontaneous human double-talk",
        "capture_report_sha256": sha(args.capture / "result.json"),
        "windows": {},
        "items": [],
        "sources": {},
    }
    for name, (a, b) in windows.items():
        raw = audio["microphone"][a:b]
        clean = audio["clean"][a:b]
        report["windows"][name] = {
            "start": a,
            "end": b,
            "raw_vad": window_metrics(capture["probabilities"], a, b, "raw"),
            "clean_vad": window_metrics(capture["probabilities"], a, b, "clean"),
            "attenuation_db": float(
                10 * np.log10((np.mean(raw**2) + 1e-12) / (np.mean(clean**2) + 1e-12))
            ),
        }
    report["echo_admission_pass"] = (
        report["windows"]["far_only"]["clean_vad"]["speech_frames"] == 0
    )
    old = args.root.parent / "vf102-linux-native"
    stt = args.root.parent / "vf100-cuda-stt"
    frozen = args.output / "executed-source"
    frozen.mkdir()
    for path in (
        Path(__file__),
        old / "runtime/stt/cuda_resident.py",
        stt / "cuda_stream.py",
        args.root / "scripts/probe_linux_acoustic_stt.py",
    ):
        report["sources"][path.name] = sha(path)
        shutil.copy2(path, frozen / path.name)
    reference, rate = sf.read(stt / "panel/84-121123-0018.wav", dtype="float32")
    if rate != 16000:
        raise ValueError("control rate differs")
    expected = "Morrel suffered an exclamation of horror and surprise to escape him"
    report["expected_text"] = expected
    jobs = [("direct_control", reference)]
    for name in ("near_only", "double_talk"):
        a, b = windows[name]
        jobs.extend(
            [
                (name + "_raw", audio["microphone"][a:b]),
                (name + "_clean", audio["clean"][a:b]),
            ]
        )
    child = subprocess.Popen(
        [
            str(stt / ".venv/bin/python"),
            str(old / "runtime/stt/cuda_resident.py"),
            "--root",
            str(stt),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    events = queue.Queue()
    messages = []

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

    thread = threading.Thread(target=receive, daemon=True)
    thread.start()

    def send(op, **fields):
        child.stdin.write(json.dumps({"op": op, **fields}) + "\n")
        child.stdin.flush()

    try:
        ready = events.get(timeout=60)
        if ready["event"] != "ready":
            raise RuntimeError(ready)
        report["ready"] = ready
        for name, pcm in jobs:
            path = args.output / (name + ".wav")
            sf.write(path, pcm, 16000, subtype="FLOAT")
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
                    "samples": len(pcm),
                    "sha256": sha(path),
                    "final": row,
                    "score": word_error(expected, row["text"]),
                }
            )
        send("close")
        if child.wait(timeout=10) != 0:
            raise RuntimeError("worker close failed")
        by_name = {item["name"]: item for item in report["items"]}
        baseline = by_name["direct_control"]["final"]["text"]
        report["transcript_preserved"] = all(
            by_name[name]["final"]["text"] == baseline
            for name in ("near_only_raw", "near_only_clean", "double_talk_clean")
        )
        far_path = (
            args.root.parent
            / "vf101-cuda-panel/cached-r1/items/interruption-recovery-03/streamed.wav"
        )
        if sha(far_path) != capture["stimuli"]["far_sha256"]:
            raise ValueError("recovery source identity differs")
        far, far_rate = sf.read(far_path, dtype="float32")
        if far_rate != 24000:
            raise ValueError("recovery sample rate differs")
        report["native_output_pass"] = output_pass(capture, len(far))
        report["status"] = (
            "passed_development_gate"
            if report["echo_admission_pass"]
            and report["transcript_preserved"]
            and report["native_output_pass"]
            else "failed_development_gate"
        )
    except Exception as exc:  # noqa: BLE001 - retained model/evaluation failure
        report.update(status="failed", error=repr(exc))
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
        thread.join(3)
        report["stt_events"] = messages
        (args.output / "result.json").write_text(
            json.dumps(report, indent=2, allow_nan=False)
        )
    print(
        json.dumps(
            {
                "status": report["status"],
                "windows": report["windows"],
                "items": [
                    {"name": i["name"], "text": i["final"]["text"], "score": i["score"]}
                    for i in report["items"]
                ],
            }
        )
    )
    return 0 if report["status"] == "passed_development_gate" else 2


if __name__ == "__main__":
    raise SystemExit(main())
