"""CPU-only integrity and acceptance audit of retained physical echo evidence."""

import argparse
import hashlib
import json
import warnings
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from scripts.evaluate_linux_echo import output_pass, window_metrics
from scripts.probe_linux_acoustic_stt import word_error


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def pcm(path):
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            category=wavfile.WavFileWarning,
            message="Chunk .* not understood, skipping it",
        )
        rate, samples = wavfile.read(path)
    require(
        rate == 16000 and samples.ndim == 1 and np.isfinite(samples).all(),
        "invalid PCM",
    )
    return samples


def source_binding(directory, record, key="sources"):
    for name, digest in record[key].items():
        require(
            sha(directory / "executed-source" / Path(name).name) == digest,
            "executed source differs: " + name,
        )


def capture(directory):
    record = json.loads((directory / "result.json").read_text())
    source_binding(directory, record)
    require(record.get("restoration_verified") is True, "restoration not verified")
    require(
        json.loads((directory / "routing-before.json").read_text())
        == json.loads((directory / "routing-after.json").read_text()),
        "routing differs",
    )
    audio = {}
    for name, item in record["recordings"].items():
        path = directory / (name + ".wav")
        require(sha(path) == item["sha256"], "recording hash differs")
        audio[name] = pcm(path)
        require(len(audio[name]) == item["samples"], "sample count differs")
    complete = record["status"] == "captured_for_echo_evaluation"
    if complete:
        require(len(audio["microphone"]) == len(audio["clean"]), "clean tail lost")
        for state, key in zip(
            record["native_states"], ("microphone", "reference"), strict=True
        ):
            prefix = (
                record.get("input_origin", {}).get(key + "_prefix_samples", 0)
                if record.get("input_origin", {}).get("recordings_rebased")
                else 0
            )
            require(
                prefix == len(audio.get("startup-" + key, [])),
                "startup prefix not preserved",
            )
            require(
                state["captured"]
                == state["read"]
                == len(audio[key]) + prefix
                == state["input_target"],
                "admitted input differs",
            )
            require(
                not state["failed"]
                and not state["capture_holes"]
                and not state["input_available"]
                and state["input_finished"],
                "native input not closed",
            )
        if "input_origin" in record:
            prefix = (
                0
                if record["input_origin"].get("recordings_rebased")
                else record["input_origin"]["reference_prefix_samples"]
            )
            require(
                0 <= prefix < 16000
                and len(audio["reference"]) - prefix >= len(audio["microphone"]),
                "missing actual render reference",
            )
    return record, audio


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    captures, rows = {}, []
    for name in (
        "near-check-r1",
        "duplex-r1",
        "duplex-r2",
        "duplex-r3",
        "duplex-r4",
        "interruption-r1",
    ):
        directory = args.root / name
        record, audio = capture(directory)
        captures[sha(directory / "result.json")] = (name, record, audio)
        rows.append(
            {
                "capture": name,
                "status": record["status"],
                "restoration_verified": True,
                "samples": len(audio["microphone"]),
                "clean_samples": len(audio["clean"]),
            }
        )
    evaluations = []
    for name in ("evaluation-r1", "evaluation-r4", "evaluation-interruption-r1"):
        directory = args.root / name
        record = json.loads((directory / "result.json").read_text())
        source_binding(directory, record)
        capture_name, source, audio = captures[record["capture_report_sha256"]]
        for window in record["windows"].values():
            for key in ("raw", "clean"):
                require(
                    window[key + "_vad"]
                    == window_metrics(
                        source["probabilities"], window["start"], window["end"], key
                    ),
                    "VAD window differs",
                )
        items = {}
        for item in record["items"]:
            path = directory / (item["name"] + ".wav")
            require(
                sha(path) == item["sha256"] and len(pcm(path)) == item["samples"],
                "recognizer input differs",
            )
            require(
                word_error(record["expected_text"], item["final"]["text"])
                == item["score"],
                "word score differs",
            )
            require(item["final"] in record["stt_events"], "final not observed")
            items[item["name"]] = item
            if item["name"] != "direct_control":
                window_name, channel = item["name"].rsplit("_", 1)
                window = record["windows"][window_name]
                values = audio["microphone" if channel == "raw" else "clean"][
                    window["start"] : window["end"]
                ]
                require(
                    np.array_equal(pcm(path), values),
                    "recognizer PCM not exact capture window",
                )
        if "transcript_preserved" in record:
            preserved = all(
                items[key]["final"]["text"] == items["direct_control"]["final"]["text"]
                for key in ("near_only_raw", "near_only_clean", "double_talk_clean")
            )
            require(
                preserved == record["transcript_preserved"],
                "transcript verdict differs",
            )
            require(
                output_pass(source, 136320) == record["native_output_pass"],
                "output verdict differs",
            )
        require(
            record["status"] != "passed_development_gate"
            or (
                record["transcript_preserved"]
                and record["native_output_pass"]
                and record["echo_admission_pass"]
            ),
            "false quality pass",
        )
        evaluations.append(
            {
                "evaluation": name,
                "capture": capture_name,
                "status": record["status"],
                "transcript_preserved": record.get("transcript_preserved"),
                "native_output_pass": record.get("native_output_pass"),
                "echo_admission_pass": record["echo_admission_pass"],
                "transcripts": {k: v["final"]["text"] for k, v in items.items()},
            }
        )
    ablation_dir = args.root / "delay-ablation-r1"
    ablation = json.loads((ablation_dir / "result.json").read_text())
    source_binding(ablation_dir, ablation, "source_sha256")
    require(ablation["capture_report_sha256"] in captures, "ablation capture missing")
    for row in ablation["cases"]:
        path = ablation_dir / (row["mode"] + ".wav")
        require(
            sha(path) == row["sha256"] and len(pcm(path)) == row["samples"],
            "ablation waveform differs",
        )
    result = {
        "integrity": "passed",
        "claim": "retained evidence and verdicts checked; not human qualification",
        "captures": rows,
        "evaluations": evaluations,
        "ablation_cases": len(ablation["cases"]),
        "audit_source_sha256": sha(Path(__file__)),
    }
    with args.output.open("x") as file:
        json.dump(result, file, indent=2)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
