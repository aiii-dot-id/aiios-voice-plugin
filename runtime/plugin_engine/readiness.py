"""Real bounded warm inference, before the public resident lane is admitted.

No microphone, speaker, user data or SDK events. Each probe retires its private
stream; a new session gets fresh decoder state. The carrier's startup deadline
and process reaper remain the hard bound for a native call that never returns.
"""

import math
import os
import time

import numpy as np


def resident_identity(models):
    """Current objects/processes, measured again at each real session opening."""
    recognizer = getattr(models, "recognizer", None)
    if recognizer is not None and recognizer.child.poll() is not None:
        raise RuntimeError("resident recognizer exited")
    return {
        "worker_pid": os.getpid(),
        "stt_pid": recognizer.child.pid if recognizer is not None else os.getpid(),
        "resident_object_ids": {
            "stt": id(recognizer if recognizer is not None else models.stt),
            "tts": id(models.adapter if hasattr(models, "adapter") else models.tts),
            "endpoint": id(models.endpoint),
        },
    }


def warm_models(models, backend, *, seconds=40):
    begun = time.perf_counter()
    phases = {}

    def elapsed(name, start):
        phases[name] = time.perf_counter() - start
        if time.perf_counter() - begun > seconds:
            raise TimeoutError("warm inference exceeded readiness budget")

    start = time.perf_counter()
    stream = models.stt_stream()
    try:
        # Two seconds exercises the actual streaming encoder and finalization,
        # including right context, without depending on a recorded voice.
        for _ in range(16):
            stream.push_audio(np.zeros(2000, dtype=np.float32))
        final = stream.finish()
        if final is None:
            raise RuntimeError("warm recognizer did not finalize")
    finally:
        recognizer = getattr(models, "recognizer", None)
        if recognizer is not None:
            recognizer.cancel()
            recognizer.retire()
    elapsed("stt_encode_and_finalize", start)

    start = time.perf_counter()
    stream = models.tts_stream("Ready.")
    try:
        chunk = models.tts_next(stream)
        if chunk is None:
            raise RuntimeError("warm synthesis produced no audio")
        samples, rate, _ = chunk
        samples = np.asarray(samples)
        if rate != 24000 or not samples.size or not np.isfinite(samples).all():
            raise RuntimeError("warm synthesis produced invalid audio")
        output_samples = int(samples.size)
    finally:
        close = getattr(stream, "close", None)
        if close:
            close()
    elapsed("tts_first_chunk_and_retirement", start)

    start = time.perf_counter()
    probability = models.endpoint.probability(np.zeros(16000, dtype=np.float32))
    if not np.isfinite(probability) or not 0 <= probability <= 1:
        raise RuntimeError("warm endpoint produced invalid probability")
    elapsed("semantic_endpoint", start)

    # This is the count of resident models, not identity manifest entries.
    # Windows/CUDA VAD is instantiated per session and is NOT counted here.
    count = 3
    if backend == "mlx":
        start = time.perf_counter()
        models.vad_feed(np.zeros(512, dtype=np.float32), models.vad_state())
        elapsed("resident_mlx_vad", start)
        count += 1
    if backend not in {"mlx", "cuda", "windows-pocket"}:
        raise ValueError("no readiness declaration for unrecognized backend")
    accelerator = "metal" if backend == "mlx" else "cuda"
    if models.identity.get("backend") in {"windows-pocket-cpu-stt-directml", "windows-pocket-vulkan-stt-directml"}:
        actual = models.recognizer.ready
        if (
            backend != "windows-pocket"
            or actual.get("backend") != "native-directml"
            or actual.get("providers", {}).get("encoder", [])[:1]
            != ["DmlExecutionProvider"]
        ):
            raise RuntimeError("Native recognizer readiness/provider differs")
        accelerator = "directml"
    report = {
        "models_loaded": count,
        "accelerator": accelerator,
        "probe_ms": max(1, math.ceil((time.perf_counter() - begun) * 1000)),
        "phases_seconds": phases,
        "synthetic_input_samples": 32000,
        "discarded_tts_samples": output_samples,
        **resident_identity(models),
        "scope": "loaded models and warm inference; not audio-device or quality qualification",
    }
    if models.identity.get("backend") == "windows-pocket-vulkan-stt-directml":
        if models.identity["models"]["tts"]["backend"] != "native-pocket-vulkan":
            raise RuntimeError("native TTS readiness differs")
        report["accelerators"] = {"stt": "directml", "tts": "vulkan", "endpoint": "cpu"}
    models.identity["warm_readiness"] = report
    return report
