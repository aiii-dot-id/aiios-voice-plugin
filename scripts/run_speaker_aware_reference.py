"""Run the pinned upstream streaming reference; preserve failed attempts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import wave

if __package__:
    from .score_speaker_aware import evaluate
else:
    from score_speaker_aware import evaluate

NEMO_REV = "f613eed86ed4696db0891aac4e9104337a39142c"
MODEL_REVS = {
    "nvidia/diar_streaming_sortformer_4spk-v2.1": "fafaab5faa1617a0ca52d38dd3dc4bd636800d3d",
    "nvidia/multitalker-parakeet-streaming-0.6b-v1": "8749fc71fd6e2d88ef230159bbf2aea69b524ee1",
}
MODEL_SHA256 = {
    "nvidia/diar_streaming_sortformer_4spk-v2.1": "8abd32832159c6ac1148c926b7276f35ba34582c444e559dce1f1253fea42ef8",
    "nvidia/multitalker-parakeet-streaming-0.6b-v1": "afaefe89829e201a0ee22c67a715eb5a467bd06c0704cc1579fbfb370fb5be73",
}


def digest(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def write_json(path, value):
    with path.open("x") as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.write("\n")


def group_segments(panel, rows):
    cases = {c["id"]: c for c in panel["cases"]}
    if len(cases) != len(panel["cases"]):
        raise ValueError("duplicate panel case")
    grouped = {key: [] for key in cases}
    for row in rows:
        case = cases[row["session_id"]]  # Unknown sessions must fail, not disappear.
        # One 80ms model frame of terminal timestamp quantization is allowed.
        # Larger overshoot is a clock failure. No timestamp is rewritten.
        if float(row["end_time"]) > case["samples"] / 16000 + .08:
            raise ValueError("model output exceeds source clock")
        grouped[row["session_id"]].append(row)
    return grouped


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--panel", type=Path, required=True)
    p.add_argument("--models", type=Path, required=True)
    p.add_argument("--nemo", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--device", choices=("cpu", "cuda"), required=True)
    p.add_argument("--threads", type=int, default=2)
    p.add_argument("--timeout", type=int, default=3600)
    a = p.parse_args()
    if a.threads < 1 or a.threads > 4 or a.timeout < 1:
        p.error("threads must be 1..4 and timeout positive")
    a.output = a.output.resolve()
    a.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    result = dict(status="failed", scope="unpaced recorded streaming reference; not installed/native/UID qualification",
                  device_requested=a.device, started_unix=time.time(), process_retired=False)
    child = None
    try:
        a.nemo = a.nemo.resolve()
        revision = subprocess.check_output(["git", "-C", str(a.nemo), "rev-parse", "HEAD"], text=True).strip()
        dirty = subprocess.check_output(["git", "-C", str(a.nemo), "status", "--porcelain"], text=True).strip()
        if revision != NEMO_REV or dirty:
            raise ValueError("upstream source must be pinned and clean")
        panel = json.loads(a.panel.read_text())
        models = json.loads(a.models.read_text())
        bound = {x["repo"]: x for x in models["models"]}
        if not models["verified"] or len(bound) != len(models["models"]) or set(bound) != set(MODEL_REVS):
            raise ValueError("model census changed")
        for name, model in bound.items():
            if (model["revision"] != MODEL_REVS[name] or model["sha256"] != MODEL_SHA256[name]
                    or digest(model["path"]) != model["sha256"]
                    or Path(model["path"]).stat().st_size != model["bytes"]):
                raise ValueError("model binding changed")
        inference = []
        for c in panel["cases"]:
            path = (a.panel.parent / c["audio_file"]).resolve()
            if path.stem != c["id"] or digest(path) != c["audio_sha256"]:
                raise ValueError("audio binding changed")
            with wave.open(str(path), "rb") as f:
                if (f.getframerate(), f.getnchannels(), f.getsampwidth(), f.getnframes()) != (16000, 1, 2, c["samples"]):
                    raise ValueError("audio format or sample count changed")
            inference.append(dict(audio_filepath=str(path), duration=c["samples"] / 16000))
        # The recognizer gets only file geometry. No reference text, activity,
        # speaker count, enrollment identity or ground-truth masks are supplied.
        manifest = a.output / "inference.jsonl"
        with manifest.open("x") as f:
            for row in inference:
                f.write(json.dumps(row) + "\n")
        upstream = a.nemo / "examples/asr/asr_cache_aware_streaming/speech_to_text_multitalker_streaming_infer.py"
        raw = a.output / "upstream.seglst.json"
        cmd = [sys.executable, str(upstream),
               "diar_model=" + bound["nvidia/diar_streaming_sortformer_4spk-v2.1"]["path"],
               "asr_model=" + bound["nvidia/multitalker-parakeet-streaming-0.6b-v1"]["path"],
               "manifest_file=" + str(manifest), "output_path=" + str(raw),
               "device=" + a.device, "batch_size=1", "num_workers=0", "spk_supervision=diar",
               "max_num_of_spks=4", "masked_asr=false", "parallel_speaker_strategy=true",
               "precision=32", "use_amp=false", "real_time_mode=false", "random_seed=17",
               "hydra.run.dir=" + str(a.output / "hydra")]
        env = os.environ.copy()
        env.update(PYTHONPATH=str(a.nemo), OMP_NUM_THREADS=str(a.threads), MKL_NUM_THREADS=str(a.threads),
                   HF_HUB_OFFLINE="1", TOKENIZERS_PARALLELISM="false", HYDRA_FULL_ERROR="1")
        if a.device == "cpu":
            env["CUDA_VISIBLE_DEVICES"] = ""
        # Do not persist inherited environment: it may contain credentials.
        result.update(command=cmd, nemo_revision=revision, upstream_script_sha256=digest(upstream),
                      runner_sha256=digest(__file__),
                      scorer_sha256=digest(Path(__file__).with_name("score_speaker_aware.py")),
                      panel_sha256=digest(a.panel), models_manifest_sha256=digest(a.models),
                      models=models["models"], inference_sha256=digest(manifest))
        import importlib.metadata
        result["packages"] = {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()}
        write_json(a.output / "start.json", result)
        with (a.output / "inference.log").open("x") as log:
            child = subprocess.Popen(cmd, env=env, cwd=a.output, stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True)
            result["pid"] = child.pid
            result["returncode"] = child.wait(timeout=a.timeout)
        result["process_retired"] = True
        if result["returncode"] != 0:
            raise RuntimeError("upstream inference failed; see inference.log")
        grouped = group_segments(panel, json.loads(raw.read_text()))
        write_json(a.output / "hypotheses.json", grouped)
        result["score"] = evaluate(panel, grouped)
        result["raw_output_sha256"] = digest(raw)
        result["status"] = "passed" if result["score"]["passed"] else "accuracy_failed"
    except (Exception, KeyboardInterrupt) as e:
        result["error"] = str(e) or type(e).__name__
    finally:
        if child is not None and child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=10)
            result["process_retired"] = True
        result["elapsed_seconds"] = time.monotonic() - started
        write_json(a.output / "result.json", result)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
