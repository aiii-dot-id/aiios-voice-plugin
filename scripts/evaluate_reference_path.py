"""Frozen VF105 24-case CUDA comparison and untouched VAD diagnostic."""

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from runtime.stt.evidence_client import EvidenceClient
from runtime.voice_core.acoustic_panel import IDS
from runtime.voice_core.control_vad import ControlVAD
from scripts.audit_linux_echo import capture, pcm, require, sha, source_binding
from scripts.evaluate_acoustic_panel import score, tokens
from scripts.evaluate_linux_echo import window_metrics


def baseline_matches(before, after):
    return before["event"] == after["event"] and tokens(
        before.get("text", "")
    ) == tokens(after.get("text", ""))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--diagnostic", type=Path, required=True)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    diagnostic = json.loads((args.diagnostic / "result.json").read_text())
    require(
        diagnostic["status"] == "completed_development_diagnostic"
        and [r["id"] for r in diagnostic["cases"]] == list(IDS),
        "incomplete diagnostic",
    )
    source_binding(args.diagnostic, diagnostic)
    old = json.loads((args.previous / "evaluation-r1/result.json").read_text())
    source_binding(args.previous / "evaluation-r1", old)
    old_items = {(r["id"], r["condition"]): r for r in old["items"]}
    stt = args.root.parent / "vf100-cuda-stt"
    native = args.root.parent / "vf102-linux-native"
    worker = native / "runtime/stt/cuda_resident.py"
    frozen = args.output / "executed-source"
    frozen.mkdir()
    paths = [
        Path(__file__),
        args.root / "runtime/stt/evidence_client.py",
        worker,
        stt / "cuda_stream.py",
        args.root / "runtime/voice_core/control_vad.py",
        args.root / "scripts/evaluate_acoustic_panel.py",
        args.root / "scripts/audit_linux_echo.py",
        args.root / "scripts/evaluate_linux_echo.py",
        args.root / "scripts/probe_linux_acoustic_stt.py",
    ]
    for path in paths:
        shutil.copy2(path, frozen / path.name)
    report = {
        "status": "running",
        "sources": {p.name: sha(p) for p in paths},
        "diagnostic_sha256": sha(args.diagnostic / "result.json"),
        "previous_sha256": sha(args.previous / "evaluation-r1/result.json"),
        "cases": [],
        "items": [],
    }
    client = None
    try:
        client = EvidenceClient(
            stt / ".venv/bin/python", worker, stt, args.output / "worker.log"
        )
        report["ready"] = client.ready
        for case in diagnostic["cases"]:
            identifier = case["id"]
            captured = args.previous / "capture-r2" / identifier
            require(
                sha(captured / "result.json") == case["capture_sha256"],
                "capture identity differs",
            )
            record, audio = capture(captured)
            require(record["status"] == "captured_for_echo_evaluation", "incomplete capture")
            path = args.diagnostic / (identifier + "_reference_only.wav")
            require(sha(path) == case["candidate_sha256"], "candidate differs")
            candidate = pcm(path)
            require(
                len(candidate) == len(audio["microphone"]), "candidate tail differs"
            )
            vad = ControlVAD(native)
            probabilities = [
                {"end_sample": i + 512, "clean": vad.feed(candidate[i : i + 512])}
                for i in range(0, len(candidate) // 512 * 512, 512)
            ]
            a, b = case["far_validation_samples"]
            report["cases"].append(
                {
                    "id": identifier,
                    "vad_identity": vad.identity,
                    "far_validation_vad": window_metrics(probabilities, a, b, "clean"),
                    "probabilities": probabilities,
                }
            )
            for condition in ("sustained", "interrupted"):
                a, b = case["windows"][condition]
                for variant, values in (
                    ("clean", audio["clean"]),
                    ("reference_only", candidate),
                ):
                    key = condition + "_" + variant
                    sid = identifier + "_" + key
                    values = values[a:b]
                    path = args.output / (sid + ".wav")
                    wavfile.write(path, 16000, values.astype(np.float32))
                    terminal = client.recognize(sid, values)
                    item = {
                        "id": identifier,
                        "condition": key,
                        "reference": case["reference"],
                        "window": [a, b],
                        "samples": len(values),
                        "sha256": sha(path),
                        "result": terminal,
                        "score": score(case["reference"], terminal),
                    }
                    if variant == "clean":
                        earlier = old_items[identifier, key]
                        previous_wav = args.previous / "evaluation-r1" / (sid + ".wav")
                        require(
                            sha(previous_wav) == earlier["sha256"]
                            and np.array_equal(pcm(previous_wav), values),
                            "baseline PCM drift",
                        )
                        item["baseline_repeatability"] = baseline_matches(
                            earlier["result"], terminal
                        )
                    report["items"].append(item)
                    print(
                        json.dumps(
                            {
                                "id": identifier,
                                "condition": key,
                                "text": terminal.get("text"),
                                "score": item["score"],
                                "baseline_repeatability": item.get(
                                    "baseline_repeatability"
                                ),
                            }
                        ),
                        flush=True,
                    )
                    (args.output / "progress.json").write_text(
                        json.dumps(report, indent=2)
                    )
        client.close()
        report["status"] = "evaluated_development_diagnostic"
    except Exception as exc:  # noqa: BLE001 - preserve incomplete/error comparisons
        report.update(status="failed", error=repr(exc))
    finally:
        if client:
            client.close(abort=True)
            report.update(client.record())
        (args.output / "result.json").write_text(
            json.dumps(report, indent=2, allow_nan=False)
        )
    return 0 if report["status"] == "evaluated_development_diagnostic" else 2


if __name__ == "__main__":
    raise SystemExit(main())
