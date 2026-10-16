"""Frozen physical controls; all transcripts retained, no candidate selection."""

import argparse
import json
import re
import shutil
from pathlib import Path

from runtime.stt.evidence_client import EvidenceClient
from runtime.voice_core.acoustic_panel import IDS, source
from runtime.voice_core.interrupt_prefix import preserve
from scripts.audit_linux_echo import capture, sha
from scripts.evaluate_linux_echo import window_metrics
from scripts.probe_linux_acoustic_stt import word_error


def tokens(text):
    return re.findall(r"[a-z0-9]+", text.casefold())


def score(reference, terminal):
    text = terminal.get("text", "")
    return {
        **word_error(reference, text),
        "prefix_tokens": tokens(text)[:3],
        "reference_prefix": tokens(reference)[:3],
        "prefix_preserved": tokens(text)[:3] == tokens(reference)[:3],
        "exact_normalized": tokens(reference) == tokens(text),
        "terminal": terminal["event"],
    }


def window(record, number):
    events = record["events"]
    start = next(
        e["capture_samples"]
        for e in events
        if e["event"] == "near_start" and e["number"] == number
    )
    end = next(
        e["capture_samples"]
        for e in events
        if e["event"] == "near_end" and e["number"] == number
    )
    return max(0, start - 4096), end + 10240


def main():
    import soundfile as sf

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    stt = args.root.parent / "vf100-cuda-stt"
    worker = args.root.parent / "vf102-linux-native/runtime/stt/cuda_resident.py"
    frozen = args.output / "executed-source"
    frozen.mkdir()
    paths = [
        Path(__file__),
        args.root / "runtime/stt/evidence_client.py",
        args.root / "runtime/voice_core/acoustic_panel.py",
        args.root / "runtime/voice_core/interrupt_prefix.py",
        worker,
        stt / "cuda_stream.py",
        args.root / "scripts/audit_linux_echo.py",
        args.root / "scripts/evaluate_linux_echo.py",
        args.root / "scripts/probe_linux_acoustic_stt.py",
    ]
    report = {
        "status": "running",
        "scope": "six-speaker physical development panel, not human qualification",
        "sources": {p.name: sha(p) for p in paths},
        "cases": [],
        "items": [],
    }
    for p in paths:
        shutil.copy2(p, frozen / p.name)
    panel = json.loads((args.capture / "result.json").read_text())
    if (
        panel["status"] != "captured_for_evaluation"
        or tuple(panel["ids"]) != IDS
        or len(panel["cases"]) != len(IDS)
    ):
        raise ValueError("capture panel incomplete")
    client = None
    try:
        client = EvidenceClient(
            stt / ".venv/bin/python", worker, stt, args.output / "worker.log"
        )
        report["ready"] = client.ready
        for identifier in IDS:
            directory = args.capture / identifier
            record, audio = capture(directory)
            path, spec = source(stt / "panel", identifier)
            direct, rate = sf.read(path, dtype="float32")
            if (
                rate != 16000
                or record["near_id"] != identifier
                or record["stimuli"]["near_sha256"] != spec["audio_sha256"]
            ):
                raise ValueError("source binding differs")
            jobs = [("direct", direct, None)]
            case = {
                "id": identifier,
                "capture_sha256": sha(directory / "result.json"),
                "windows": {},
            }
            for number, condition in (
                (1, "near"),
                (2, "sustained"),
                (3, "interrupted"),
            ):
                a, b = window(record, number)
                if b > len(audio["microphone"]):
                    raise ValueError("missing recognition tail")
                case["windows"][condition] = [a, b]
                for channel, key in (("raw", "microphone"), ("clean", "clean")):
                    jobs.append((condition + "_" + channel, audio[key][a:b], [a, b]))
            far_start = next(
                e["capture_samples"]
                for e in record["events"]
                if e["event"] == "far_start" and e["number"] == 1
            )
            far_end = next(
                e["capture_samples"]
                for e in record["events"]
                if e["event"] == "near_start" and e["number"] == 2
            )
            case["far_only_vad"] = window_metrics(
                record["probabilities"], far_start, far_end, "clean"
            )
            stops = [e for e in record["events"] if e["event"] == "vad_stop"]
            ends = [e for e in record["events"] if e["event"] == "far_end"]
            case["stop_epochs"] = [e["number"] for e in stops]
            triggers = [e for e in stops if e["number"] == 3]
            a, b = case["windows"]["interrupted"]
            if len(triggers) == 1:
                hybrid, prefix = preserve(
                    audio["microphone"], audio["clean"], triggers[0]["vad_end_sample"]
                )
                case["prefix_candidate"] = prefix
                jobs.append(("interrupted_preserved", hybrid[a:b], [a, b]))
            else:
                case["prefix_candidate"] = {"error": "missing unique interruption"}
                jobs.append(("interrupted_preserved", None, [a, b]))
            case["native_output_pass"] = (
                case["stop_epochs"] == [3]
                and [e["number"] for e in ends] == [1, 2, 4]
                and all(e["written"] == 136320 and not e["underflows"] for e in ends)
                and not any(
                    e["event"] == "underflow_observed" for e in record["events"]
                )
            )
            report["cases"].append(case)
            for name, values, bounds in jobs:
                sid = identifier + "_" + name
                path = args.output / (sid + ".wav")
                if values is None:
                    terminal = {
                        "event": "error",
                        "sid": sid,
                        "source": "capture_trigger",
                        "message": "missing unique interruption",
                    }
                else:
                    sf.write(path, values, 16000, subtype="FLOAT")
                    terminal = client.recognize(sid, values)
                item = {
                    "id": identifier,
                    "condition": name,
                    "window": bounds,
                    "samples": len(values) if values is not None else 0,
                    "sha256": sha(path) if values is not None else None,
                    "reference": spec["reference_text"],
                    "result": terminal,
                    "score": score(spec["reference_text"], terminal),
                }
                report["items"].append(item)
                print(
                    json.dumps(
                        {
                            "id": identifier,
                            "condition": name,
                            "text": terminal.get("text"),
                            "score": item["score"],
                        }
                    ),
                    flush=True,
                )
                (args.output / "progress.json").write_text(json.dumps(report, indent=2))
        client.close()
        report["status"] = "evaluated_development_panel"
    except Exception as exc:  # noqa: BLE001 - retain incomplete comparisons
        report.update(status="failed", error=repr(exc))
    finally:
        if client:
            client.close(abort=True)
            report.update(client.record())
        (args.output / "result.json").write_text(
            json.dumps(report, indent=2, allow_nan=False)
        )
    return 0 if report["status"] == "evaluated_development_panel" else 2


if __name__ == "__main__":
    raise SystemExit(main())
