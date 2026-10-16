"""VF106: exact old replay and latency-aware replay, without audio devices."""

import argparse
import json
import shutil
import time
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from runtime.voice_core.acoustic_panel import IDS
from runtime.voice_core.control_vad import ControlVAD
from runtime.voice_core.echo_delay_candidates import configure
from runtime.voice_core.interrupt_prefix import preserve
from runtime.voice_core.latency_aligned_echo import LatencyAlignedEcho
from runtime.voice_core.linux_echo import load_linux_echo
from scripts.audit_linux_echo import capture, require, sha, source_binding
from scripts.evaluate_acoustic_panel import window
from scripts.evaluate_linux_echo import window_metrics

CONDITIONS = (
    "sustained_clean",
    "sustained_aligned",
    "interrupted_clean",
    "interrupted_aligned",
    "interrupted_aligned_prefix",
)


def replay(front, mic, render, vad, *, legacy=False):
    """Return exact real PCM and causal source/availability accounting."""
    size = len(mic)
    padding = (-size) % 512 if legacy else 0
    near, far = np.pad(mic, (0, padding)), np.pad(render, (0, padding))
    chunks, rows, times = [], [], []
    produced = 0

    def take(parts, available, finished):
        nonlocal produced
        for part in parts:
            end = min(size, produced + len(part))
            valid = part[: end - produced]
            if not len(valid):
                continue
            require(end <= available, "output consumes unavailable input")
            probability = vad.feed(valid) if len(valid) == 512 else None
            rows.append(
                {
                    "start_sample": produced,
                    "end_sample": end,
                    "input_available": available,
                    "finish_only": finished,
                    "clean": probability,
                }
            )
            chunks.append(valid.copy())
            produced = end

    for i in range(0, len(near), 512):
        started = time.perf_counter()
        parts = front.push(near[i : i + 512], far[i : i + 512])
        times.append((time.perf_counter() - started) * 1000)
        take(parts, min(size, i + 512), False)
    take(front.finish(), size, True)
    require(produced == size, "replay dropped real tail")
    return (
        np.concatenate(chunks),
        rows,
        {
            "dsp_p95_ms": float(np.percentile(times, 95)),
            "dsp_max_ms": max(times),
            "transport_padding": padding,
            "dsp_padding": front.padding_samples,
            "vad_unscored_tail_samples": sum(
                r["end_sample"] - r["start_sample"] for r in rows if r["clean"] is None
            ),
        },
    )


def intervals(record):
    events = record["events"]

    def at(kind, number):
        return next(
            e["capture_samples"]
            for e in events
            if e["event"] == kind and e["number"] == number
        )

    return {
        "far_only": [at("far_start", 1), at("near_start", 2)],
        "recovery": [at("far_start", 4), at("far_end", 4)],
        "interrupted": list(window(record, 3)),
    }


def vad_summary(rows, bounds):
    measured = [r for r in rows if r["clean"] is not None]
    output = {}
    for name, (start, end) in bounds.items():
        positives = [
            r for r in measured if start < r["end_sample"] <= end and r["clean"] >= 0.5
        ]
        output[name] = {
            **window_metrics(measured, start, end, "clean"),
            "first_positive": positives[0] if positives else None,
        }
    return output


def main():
    parser = argparse.ArgumentParser()
    for name in ("root", "previous", "plan", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    frozen = args.output / "executed-source"
    frozen.mkdir()
    files = [Path(__file__), args.plan] + [
        args.root / p
        for p in (
            "runtime/voice_core/latency_aligned_echo.py",
            "runtime/voice_core/echo.py",
            "runtime/voice_core/echo_delay_candidates.py",
            "runtime/voice_core/linux_echo.py",
            "runtime/voice_core/control_vad.py",
            "runtime/voice_core/acoustic_panel.py",
            "runtime/voice_core/interrupt_prefix.py",
            "scripts/audit_linux_echo.py",
            "scripts/evaluate_acoustic_panel.py",
            "scripts/evaluate_linux_echo.py",
            "scripts/probe_linux_acoustic_stt.py",
        )
    ]
    for path in files:
        shutil.copy2(path, frozen / path.name)
    old = json.loads((args.previous / "evaluation-r1/result.json").read_text())
    source_binding(args.previous / "evaluation-r1", old)
    references = {r["id"]: r["reference"] for r in old["items"]}
    report = {
        "status": "running",
        "sources": {p.name: sha(p) for p in files},
        "previous_sha256": sha(args.previous / "evaluation-r1/result.json"),
        "cases": [],
    }
    try:
        for identifier in IDS:
            directory = args.previous / "capture-r2" / identifier
            record, audio = capture(directory)
            require(record["input_origin"]["recordings_rebased"], "capture not rebased")
            mic = audio["microphone"]
            render = audio["reference"][: len(mic)]
            case = {
                "id": identifier,
                "reference": references[identifier],
                "capture_sha256": sha(directory / "result.json"),
                "samples": len(mic),
                "windows": {
                    label: list(window(record, number))
                    for number, label in (
                        (1, "near"),
                        (2, "sustained"),
                        (3, "interrupted"),
                    )
                },
                "intervals": intervals(record),
                "variants": {},
            }
            aligned = None
            for variant in ("clean", "aligned"):
                original, identity = load_linux_echo(args.root)
                front = (
                    configure(original, "native")
                    if variant == "clean"
                    else LatencyAlignedEcho(original.processor)
                )
                vad = ControlVAD(args.root.parent / "vf102-linux-native")
                values, rows, timing = replay(
                    front, mic, render, vad, legacy=variant == "clean"
                )
                if variant == "clean":
                    require(
                        np.array_equal(values, audio["clean"]),
                        "old native replay differs from physical capture",
                    )
                else:
                    aligned = values
                    a, b = case["windows"]["near"]
                    require(
                        np.array_equal(values[a:b], mic[a:b]),
                        "near-only bypass differs",
                    )
                path = args.output / (identifier + "_" + variant + ".wav")
                wavfile.write(path, 16000, values)
                case["variants"][variant] = {
                    "sha256": sha(path),
                    "native_identity": identity,
                    "vad_identity": vad.identity,
                    "blocks": rows,
                    "vad": vad_summary(rows, case["intervals"]),
                    **timing,
                }
            stops = [
                e
                for e in record["events"]
                if e["event"] == "vad_stop" and e["number"] == 3
            ]
            require(len(stops) == 1, "missing unique original stop")
            trigger = stops[0]["vad_end_sample"]
            hybrid, metadata = preserve(mic, aligned, trigger)
            path = args.output / (identifier + "_aligned_prefix.wav")
            wavfile.write(path, 16000, hybrid)
            case["prefix"] = {
                **metadata,
                "original_trigger_sample": trigger,
                "sha256": sha(path),
            }
            report["cases"].append(case)
            (args.output / "progress.json").write_text(json.dumps(report, indent=2))
            print(
                json.dumps(
                    {
                        "id": identifier,
                        "old_parity": True,
                        "near_bypass": True,
                        "samples": len(mic),
                    }
                ),
                flush=True,
            )
        report["status"] = "completed_frozen_latency_replay"
    except Exception as exc:  # noqa: BLE001 - retain failed experiments
        report.update(status="failed", error=repr(exc))
    (args.output / "result.json").write_text(
        json.dumps(report, indent=2, allow_nan=False)
    )
    return 0 if report["status"] == "completed_frozen_latency_replay" else 2


if __name__ == "__main__":
    raise SystemExit(main())
