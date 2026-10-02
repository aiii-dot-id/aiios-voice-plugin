"""Frozen 36-case recognition comparison, including unmodified near-only speech."""

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from runtime.stt.evidence_client import EvidenceClient
from runtime.voice_core.acoustic_panel import IDS
from scripts.audit_linux_echo import capture, pcm, require, sha, source_binding
from scripts.evaluate_acoustic_panel import score
from scripts.evaluate_reference_path import baseline_matches
from scripts.replay_localvqe_panel import CONDITIONS


def main():
    parser = argparse.ArgumentParser()
    for name in ("root", "previous", "replay", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    replay = json.loads((args.replay / "result.json").read_text())
    require(
        replay["status"] == "completed_pretrained_echo_replay"
        and [r["id"] for r in replay["cases"]] == list(IDS),
        "incomplete candidate replay",
    )
    source_binding(args.replay, replay)
    old_path = args.previous / "evaluation-r1/result.json"
    require(sha(old_path) == replay["previous_sha256"], "baseline changed")
    old = json.loads(old_path.read_text())
    source_binding(old_path.parent, old)
    old_items = {(r["id"], r["condition"]): r for r in old["items"]}
    stt = args.root.parent / "vf100-cuda-stt"
    worker = args.root.parent / "vf102-linux-native/runtime/stt/cuda_resident.py"
    for path in (
        worker,
        stt / "cuda_stream.py",
        args.root / "runtime/stt/evidence_client.py",
    ):
        require(sha(path) == old["sources"][path.name], "recognizer runtime changed")
    files = [Path(__file__), worker, stt / "cuda_stream.py"] + [
        args.root / p
        for p in (
            "runtime/stt/evidence_client.py",
            "runtime/voice_core/acoustic_panel.py",
            "scripts/replay_localvqe_panel.py",
            "scripts/replay_latency_panel.py",
            "scripts/evaluate_reference_path.py",
            "scripts/audit_linux_echo.py",
            "scripts/evaluate_acoustic_panel.py",
            "scripts/evaluate_linux_echo.py",
            "scripts/probe_linux_acoustic_stt.py",
        )
    ]
    frozen = args.output / "executed-source"
    frozen.mkdir()
    for path in files:
        shutil.copy2(path, frozen / path.name)
    report = {
        "status": "running",
        "sources": {p.name: sha(p) for p in files},
        "replay_sha256": sha(args.replay / "result.json"),
        "previous_sha256": sha(old_path),
        "items": [],
    }
    client = None
    try:
        client = EvidenceClient(
            stt / ".venv/bin/python", worker, stt, args.output / "worker.log"
        )
        report["ready"] = client.ready
        require(
            client.ready["model_manifest_sha256"]
            == old["ready"]["model_manifest_sha256"],
            "recognizer checkpoint changed",
        )
        for case in replay["cases"]:
            identifier = case["id"]
            directory = args.previous / "capture-r2" / identifier
            require(
                sha(directory / "result.json") == case["capture_sha256"],
                "capture differs",
            )
            _record, audio = capture(directory)
            path = args.replay / (identifier + "_localvqe.wav")
            require(sha(path) == case["sha256"], "candidate differs")
            candidate = pcm(path)
            for condition in CONDITIONS:
                name, variant = condition.split("_", 1)
                a, b = case["windows"][name]
                values = (audio["clean"] if variant == "clean" else candidate)[a:b]
                require(len(values) == b - a, "missing input tail")
                sid = identifier + "_" + condition
                path = args.output / (sid + ".wav")
                wavfile.write(path, 16000, values.astype(np.float32))
                if variant == "clean":
                    before = old_items[identifier, condition]
                    path = old_path.parent / (sid + ".wav")
                    require(
                        sha(path) == before["sha256"]
                        and np.array_equal(pcm(path), values),
                        "baseline PCM changed",
                    )
                terminal = client.recognize(sid, values)
                item = {
                    "id": identifier,
                    "condition": condition,
                    "reference": case["reference"],
                    "window": [a, b],
                    "samples": len(values),
                    "sha256": sha(args.output / (sid + ".wav")),
                    "result": terminal,
                    "score": score(case["reference"], terminal),
                }
                if variant == "clean":
                    item["baseline_repeatability"] = baseline_matches(
                        before["result"], terminal
                    )
                report["items"].append(item)
                (args.output / "progress.json").write_text(json.dumps(report, indent=2))
                print(
                    json.dumps(
                        {
                            "id": identifier,
                            "condition": condition,
                            "text": terminal.get("text"),
                            "score": item["score"],
                            "baseline_repeatability": item.get(
                                "baseline_repeatability"
                            ),
                        }
                    ),
                    flush=True,
                )
        client.close()
        report["status"] = "completed_pretrained_echo_comparison"
    except Exception as exc:  # noqa: BLE001 - retain every incomplete/error case
        report.update(status="failed", error=repr(exc))
    finally:
        if client:
            client.close(abort=True)
            report.update(client.record())
        (args.output / "result.json").write_text(
            json.dumps(report, indent=2, allow_nan=False)
        )
    return 0 if report["status"] == "completed_pretrained_echo_comparison" else 2


if __name__ == "__main__":
    raise SystemExit(main())
