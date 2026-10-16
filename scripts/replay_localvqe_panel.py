"""Whole continuous physical recordings through the pinned learned AEC stream."""

import argparse
import json
import shutil
from pathlib import Path

from scipy.io import wavfile

from runtime.voice_core.acoustic_panel import IDS
from runtime.voice_core.control_vad import ControlVAD
from runtime.voice_core.localvqe import LocalVQEStream, NativeLocalVQE
from scripts.audit_linux_echo import capture, require, sha, source_binding
from scripts.evaluate_acoustic_panel import window
from scripts.replay_latency_panel import intervals, replay, vad_summary

CONDITIONS = tuple(
    f"{name}_{variant}"
    for name in ("near", "sustained", "interrupted")
    for variant in ("clean", "localvqe")
)


def main():
    parser = argparse.ArgumentParser()
    for name in ("root", "previous", "plan", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    proof_path = args.root / "native-proof-r1/result.json"
    proof = json.loads(proof_path.read_text())
    require(
        proof["status"] == "passed_native_origin_reset_and_tail",
        "native gate incomplete",
    )
    source_binding(proof_path.parent, proof)
    old_path = args.previous / "evaluation-r1/result.json"
    old = json.loads(old_path.read_text())
    source_binding(old_path.parent, old)
    refs = {r["id"]: r["reference"] for r in old["items"]}
    files = [Path(__file__), args.plan] + [
        args.root / p
        for p in (
            "runtime/voice_core/localvqe.py",
            "runtime/voice_core/control_vad.py",
            "runtime/voice_core/acoustic_panel.py",
            "runtime/voice_core/interrupt_prefix.py",
            "scripts/audit_linux_echo.py",
            "scripts/evaluate_acoustic_panel.py",
            "scripts/replay_latency_panel.py",
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
        "native_proof_sha256": sha(proof_path),
        "previous_sha256": sha(old_path),
        "cases": [],
    }
    try:
        for identifier in IDS:
            cap = args.previous / "capture-r2" / identifier
            record, audio = capture(cap)
            require(record["input_origin"]["recordings_rebased"], "unrebased capture")
            mic, render = (
                audio["microphone"],
                audio["reference"][: len(audio["microphone"])],
            )
            native = NativeLocalVQE(args.root)
            try:
                require(
                    native.identity == proof["identity"], "native proof binding differs"
                )
                vad = ControlVAD(args.root.parent / "vf102-linux-native")
                output, blocks, timing = replay(
                    LocalVQEStream(native), mic, render, vad
                )
            finally:
                native.close()
            path = args.output / (identifier + "_localvqe.wav")
            wavfile.write(path, 16000, output)
            bounds = intervals(record)
            case = {
                "id": identifier,
                "reference": refs[identifier],
                "capture_sha256": sha(cap / "result.json"),
                "samples": len(mic),
                "sha256": sha(path),
                "identity": native.identity,
                "vad_identity": vad.identity,
                "windows": {
                    name: list(window(record, number))
                    for number, name in (
                        (1, "near"),
                        (2, "sustained"),
                        (3, "interrupted"),
                    )
                },
                "intervals": bounds,
                "blocks": blocks,
                "vad": vad_summary(blocks, bounds),
                **timing,
            }
            report["cases"].append(case)
            (args.output / "progress.json").write_text(json.dumps(report, indent=2))
            print(
                json.dumps(
                    {
                        "id": identifier,
                        "samples": len(output),
                        "p95_ms": timing["dsp_p95_ms"],
                        "far_only_positive": case["vad"]["far_only"]["speech_frames"],
                        "recovery_positive": case["vad"]["recovery"]["speech_frames"],
                    }
                ),
                flush=True,
            )
        report["status"] = "completed_pretrained_echo_replay"
    except Exception as exc:  # noqa: BLE001 - evidence survives a failed case
        report.update(status="failed", error=repr(exc))
    (args.output / "result.json").write_text(
        json.dumps(report, indent=2, allow_nan=False)
    )
    return 0 if report["status"] == "completed_pretrained_echo_replay" else 2


if __name__ == "__main__":
    raise SystemExit(main())
