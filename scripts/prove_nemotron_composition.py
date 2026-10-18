#!/usr/bin/env python3
"""Private recorded-audio gate; reference text is read only AFTER inference.

Runs the same native composition on each platform. This is not installed,
physical-audio or general UID qualification. No audio is bundled by this tool.
"""
import argparse
import array
import hashlib
import itertools
import json
import os
import re
import subprocess
import sys
import time
import wave
from collections import Counter
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def words(text):
    return re.findall(r"[A-Z0-9]+", text.upper())


def bindings(args):
    files = {"probe": args.probe, "model": args.model, "panel": args.panel,
             "ort_library": args.ort_library, "verifier": Path(__file__)}
    for name in ("mel.f32", "tokens.json"):
        files["graphs/" + name] = args.graphs / name
    for name in ("asr_preencode", "asr_encoder", "asr_decoder", "asr_joiner"):
        folder = args.graphs / name
        if not (folder / "model.onnx").is_file():
            raise ValueError("missing ASR graph")
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                files["graphs/" + path.relative_to(args.graphs).as_posix()] = path
    directory = args.native_root / getattr(args, "native_library_subdir", "lib")
    libraries = [p for p in directory.iterdir()
                 if p.is_file() and not p.is_symlink()]
    if not any("nemo_speech_asr_c" in p.name for p in libraries):
        raise ValueError("native library inventory missing")
    files.update({"native/" + p.name: p for p in libraries})
    for root_index, root in enumerate(getattr(args, "encoder_runtime", [])):
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                files[f"encoder_runtime/{root_index}/" + path.relative_to(root).as_posix()] = path
    return {name: {"bytes": path.stat().st_size, "sha256": digest(path)}
            for name, path in files.items()}


def cuda_placement(events):
    """Require learned compute on CUDA; permit CPU integer shapes/bool masks.

    This proves this encoder invocation, not placement of the other models.
    Unknown providers, missing type metadata and CPU floating compute fail.
    """
    counts = Counter()
    cpu_ops = {"Shape", "Gather", "Add", "Slice", "Squeeze", "Neg", "Unsqueeze",
               "Concat", "Reshape", "Cast", "Tile", "Transpose", "And", "Sub"}
    for event in events:
        args = event.get("args", {})
        provider = args.get("provider")
        if not provider:
            continue
        op = args.get("op_name")
        if provider not in {"CPUExecutionProvider", "CUDAExecutionProvider"} or not op:
            raise ValueError("unknown encoder execution placement")
        counts[provider + "/" + op] += 1
        if provider == "CPUExecutionProvider":
            if op not in cpu_ops:
                raise ValueError("neural or unknown encoder operator on CPU")
            for key in ("input_type_shape", "output_type_shape"):
                tensors = args.get(key)
                if not tensors or any(not tensor or not set(tensor) <= {"int64", "int32", "bool"}
                                      for tensor in tensors):
                    raise ValueError("unverified or floating encoder computation on CPU")
    for op in ("MatMul", "Conv", "LayerNormalization", "Softmax"):
        if not counts["CUDAExecutionProvider/" + op]:
            raise ValueError("missing neural encoder CUDA execution")
    return dict(sorted(counts.items()))


def distance(a, b):
    row = list(range(len(b) + 1))
    for i, x in enumerate(a):
        nxt = [i + 1]
        for j, y in enumerate(b):
            nxt.append(min(nxt[-1] + 1, row[j + 1] + 1, row[j] + (x != y)))
        row = nxt
    return row[-1]


def attribution_evidence(data):
    """Refuse old raw-island reports or contradictory whole-final evidence."""
    if data.get("attribution_policy") != "whole-final-coverage-v1":
        raise ValueError("missing production attribution coverage decision")
    rows = data.get("attribution_evidence")
    if not isinstance(rows, list) or len(rows) != len(data["tracks"]):
        raise ValueError("attribution track census")
    for track, row in enumerate(rows):
        if type(row.get("track")) is not int or row["track"] != track:
            raise ValueError("attribution track order")
        count, reason = row.get("samples"), row.get("reason")
        if type(count) is not int or not (count == 0 or 32000 <= count <= 160000):
            raise ValueError("attribution sample extent")
        if not isinstance(reason, str) or bool(reason) != (count == 0):
            raise ValueError("attribution disposition contradicts samples")
        for name in ("overlap_samples", "competing_uncertain_samples"):
            value = row.get(name)
            if type(value) is not int or value < 0 or (value and count):
                raise ValueError("competing activity cannot supply whole-final identity")
        regions = row.get("regions")
        if not isinstance(regions, list) or len(regions) > 128:
            raise ValueError("attribution region census")
        total, last = 0, 0
        for region in regions:
            if (not isinstance(region, list) or len(region) != 2 or
                any(type(v) is not int for v in region) or
                not last <= region[0] < region[1] <= data["samples"]):
                raise ValueError("attribution region extent/order")
            total += region[1] - region[0]
            last = region[1]
        if total != count:
            raise ValueError("attribution regions differ from selected PCM")
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("probe", "graphs", "model", "panel", "output", "native-root", "ort-library"):
        p.add_argument("--" + name, required=True, type=Path)
    p.add_argument("--gpu", required=True, type=int)
    p.add_argument("--encoder-cuda", type=int, default=-1)
    p.add_argument("--encoder-threads", type=int, default=2)
    p.add_argument("--encoder-runtime", type=Path, action="append", default=[])
    p.add_argument("--native-library-subdir", choices=("lib", "bin"), default="lib",
                   help="bound runtime library layout; Windows packages use bin")
    args = p.parse_args()
    if args.encoder_cuda < -1 or not 1 <= args.encoder_threads <= 16:
        p.error("invalid encoder configuration")
    if args.encoder_cuda >= 0 and not args.encoder_runtime:
        p.error("CUDA qualification requires bound encoder runtime directories")
    args.output.mkdir(parents=True, exist_ok=False)
    panel = json.loads(args.panel.read_text())
    vocab = json.loads((args.graphs / "tokens.json").read_text())
    rows = []
    report = {"scope": "recorded native composition, not installed or broad identity qualification",
              "probe_sha256": digest(args.probe), "model_sha256": digest(args.model),
              "panel_sha256": digest(args.panel), "gpu_index": args.gpu, "cases": rows,
              "passed": False, "completed": False, "speaker_identity_qualified": False,
              "bindings": bindings(args),
              "encoder_cuda_device": args.encoder_cuda, "encoder_threads": args.encoder_threads,
              "vulkan_disable_f16": os.environ.get("GGML_VK_DISABLE_F16")}
    (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    if not panel["cases"]:
        raise ValueError("empty acceptance panel")
    for case in panel["cases"]:
        name = case["id"]
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
            raise ValueError("unsafe case id")
        wav = args.panel.parent / case["audio_file"]
        if digest(wav) != case["audio_sha256"]:
            raise ValueError("recording binding changed")
        with wave.open(str(wav), "rb") as f:
            if (f.getnchannels(), f.getsampwidth(), f.getframerate()) != (1, 2, 16000):
                raise ValueError("expected PCM16 mono 16 kHz")
            pcm = array.array("h", f.readframes(f.getnframes()))
        if sys.byteorder != "little":
            pcm.byteswap()
        floats = array.array("f", (x / 32768 for x in pcm))
        if sys.byteorder != "little":
            floats.byteswap()
        audio = args.output / (name + ".f32")
        audio.write_bytes(floats.tobytes())
        command = [str(args.probe), str(args.graphs), str(args.graphs / "mel.f32"),
                   "--nemotron", str(args.model), str(args.gpu),
                   "--encoder-threads", str(args.encoder_threads)]
        if args.encoder_cuda >= 0:
            command += ["--encoder-cuda", str(args.encoder_cuda), "--encoder-profile",
                        str(args.output / (name + "-encoder"))]
        command.append(str(audio))
        began = time.monotonic()
        with (args.output / (name + ".jsonl")).open("w") as out, (args.output / (name + ".log")).open("w") as err:
            result = subprocess.run(command, stdout=out, stderr=err, timeout=600)
        elapsed = time.monotonic() - began
        if result.returncode:
            raise RuntimeError(f"native inference failed for {name}: {result.returncode}")
        data = json.loads((args.output / (name + ".jsonl")).read_text())
        identity = attribution_evidence(data)
        placement = None
        if args.encoder_cuda >= 0:
            profiles = list(args.output.glob(name + "-encoder_*.json"))
            if len(profiles) != 1:
                raise ValueError("missing or ambiguous encoder execution profile")
            placement = {"sha256": digest(profiles[0]),
                         "operators": cuda_placement(json.loads(profiles[0].read_text()))}
        hypothesis = [words("".join(vocab[t].replace("▁", " ") for t in tokens))
                      for tokens in data["tracks"] if tokens]
        refs = {}
        for r in case["reference"]:
            refs.setdefault(r["speaker"], []).extend(words(r["words"]))
        reference = list(refs.values())
        n = max(len(reference), len(hypothesis))
        padded = hypothesis + [[]] * (n - len(hypothesis))
        target = reference + [[]] * (n - len(reference))
        costs = [[distance(a, b) for b in padded] for a in target]
        perm = min(itertools.permutations(range(n)), key=lambda order: sum(costs[i][j] for i, j in enumerate(order)))
        errors = sum(costs[i][j] for i, j in enumerate(perm))
        total = sum(map(len, reference))
        maximum_wer = max((costs[i][perm[i]] / len(r) for i, r in enumerate(reference)), default=0)
        accepted = (errors / total <= panel["gate"]["max_case_cpwer"] and
                    maximum_wer <= panel["gate"]["max_speaker_wer"] and len(hypothesis) == len(reference))
        rows.append(dict(id=name, passed=accepted, errors=errors, reference_words=total,
                         cpwer=errors / total, max_speaker_wer=maximum_wer,
                         emitted_speakers=len(hypothesis), seconds=elapsed,
                         load_seconds=data.get("load_seconds"),
                         inference_seconds=data.get("inference_seconds"),
                         audio_seconds=len(pcm) / 16000,
                         encoder_placement=placement,
                         peak_retained_samples=data["peak_retained_samples"],
                         peak_retained_diarization_frames=data["peak_retained_diarization_frames"],
                         evidence_samples=[s["samples"] for s in identity],
                         evidence_reasons=[s["reason"] for s in identity],
                         raw_island_samples=[s["samples"] for s in data["selected_evidence"]],
                         speaker_identity_qualified=False))
        (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(rows[-1]), flush=True)
    if bindings(args) != report["bindings"]:
        raise ValueError("bound inputs changed during test")
    report["completed"] = True
    report["passed"] = all(r["passed"] for r in rows)
    (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
