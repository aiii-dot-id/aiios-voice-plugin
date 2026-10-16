#!/usr/bin/env python3
"""Measure the real native frontend with speech and a controlled echo path.

Private recordings stay external. This is a synthetic acoustic mixture of
recorded speech, NOT a browser/room or speaker-identification qualification.
No audio or transcript is written; the result binds input and library hashes.
"""
import argparse
import ctypes as ct
import hashlib
import json
import time
import wave
from pathlib import Path

import numpy as np


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    with wave.open(str(path), "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, 16000):
            raise ValueError("fixtures must be 16 kHz mono PCM16")
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    if len(x) < 16000 or not np.any(x):
        raise ValueError("fixture needs at least one second of non-silent speech")
    return x


class Frontend:
    def __init__(self, lib):
        self.lib = lib
        self.handle = ct.c_void_p()
        if lib.aii_echo_create(1, ct.byref(self.handle)):
            raise RuntimeError("AEC create failed")

    def close(self):
        self.lib.aii_echo_destroy(self.handle)

    def process(self, capture, render, missing=()):
        blocks = []
        timings = []
        for start in range(0, len(capture), 160):
            c = np.ascontiguousarray(capture[start:start+160])
            r = np.ascontiguousarray(render[start:start+160])
            target = np.empty(160, dtype=np.float32)
            count = ct.c_size_t()
            before = time.perf_counter()
            rc = self.lib.aii_echo_process(self.handle, 1, start,
                c.ctypes.data_as(ct.POINTER(ct.c_float)),
                r.ctypes.data_as(ct.POINTER(ct.c_float)), 160,
                0 if start//160 in missing else 1,
                target.ctypes.data_as(ct.POINTER(ct.c_float)), ct.byref(count))
            timings.append((time.perf_counter()-before)*1000)
            if rc:
                raise RuntimeError(f"AEC process failed at frame {start//160}: {rc}")
            if count.value not in (0, 160):
                raise RuntimeError("invalid output count")
            blocks.append(target[:count.value].copy())
        target = np.empty(160, dtype=np.float32)
        count = ct.c_size_t()
        if self.lib.aii_echo_finish(self.handle, 1,
                target.ctypes.data_as(ct.POINTER(ct.c_float)), ct.byref(count)):
            raise RuntimeError("AEC finish failed")
        blocks.append(target[:count.value].copy())
        out = np.concatenate(blocks)
        if len(out) != len(capture):
            raise RuntimeError("capture length changed")
        if not np.isfinite(out).all():
            raise RuntimeError("nonfinite output")
        return out, {"frame_ms_p50": float(np.percentile(timings, 50)),
                     "frame_ms_p95": float(np.percentile(timings, 95)),
                     "frame_ms_max": max(timings)}


def energy(x):
    return float(np.mean(np.square(x.astype(np.float64))))


def db_ratio(a, b):
    return 10*np.log10(max(a, 1e-20)/max(b, 1e-20))


def compare(near, mixed, output, start, end):
    # Report, do not conceal, algorithmic lag. AEC3's block framing delays
    # processed samples. This bounded diagnostic alignment changes no PCM.
    a = near[start:end].astype(np.float64)
    scores = []
    for lag in range(321):
        b = output[start+lag:end+lag].astype(np.float64)
        scores.append(float(np.dot(a, b) / max(np.linalg.norm(a)*np.linalg.norm(b), 1e-20)))
    lag = int(np.argmax(scores))
    b = output[start+lag:end+lag]
    improvement = db_ratio(energy(mixed[start:end]-near[start:end]), energy(b-near[start:end]))
    return {"diagnostic_alignment_samples": lag,
            "near_correlation": scores[lag],
            "near_reconstruction_improvement_db": float(improvement),
            "near_level_ratio_db": float(db_ratio(energy(b), energy(a)))}


def run(args):
    # Load the explicitly named companion before the wrapper. Record both
    # images: a wrapper hash alone says nothing about its DSP dependency.
    backend = ct.CDLL(str(args.backend_library.resolve()))
    lib = ct.CDLL(str(args.library.resolve()))
    lib.aii_echo_create.argtypes = [ct.c_uint64, ct.POINTER(ct.c_void_p)]
    lib.aii_echo_create.restype = ct.c_int
    lib.aii_echo_destroy.argtypes = [ct.c_void_p]
    lib.aii_echo_process.argtypes = [ct.c_void_p, ct.c_uint64, ct.c_uint64,
        ct.POINTER(ct.c_float), ct.POINTER(ct.c_float), ct.c_size_t,
        ct.c_uint, ct.POINTER(ct.c_float), ct.POINTER(ct.c_size_t)]
    lib.aii_echo_process.restype = ct.c_int
    lib.aii_echo_finish.argtypes = [ct.c_void_p, ct.c_uint64,
        ct.POINTER(ct.c_float), ct.POINTER(ct.c_size_t)]
    lib.aii_echo_finish.restype = ct.c_int
    frames = 2400
    size = frames*160
    def speech(path, peak):
        x = read(path)
        x = x*(peak/np.max(np.abs(x)))
        return np.resize(x, size).astype(np.float32)
    far = speech(args.render, .35)
    near = speech(args.capture, .3)
    rows = []
    def process(capture, render, missing=()):
        engine = Frontend(lib)
        try:
            return engine.process(capture, render, missing)
        finally:
            engine.close()
    out, timing = process(near, np.zeros_like(far))
    rows.append(dict(case="near_only", exact_passthrough=bool(np.array_equal(out, near)),
                     passed=bool(np.array_equal(out, near)), **timing))
    for delay_ms in (40, 120):
        echo = np.zeros_like(far)
        for gain, extra in ((.55, 0), (.18, 320), (.08, 960)):
            d = delay_ms*16+extra
            echo[d:] += gain*far[:-d]
        out, timing = process(echo, far)
        erle = db_ratio(energy(echo[160000:]), energy(out[160000:]))
        startup = db_ratio(energy(echo[:16000]), energy(out[:16000]))
        rows.append(dict(case="echo_only", delay_ms=delay_ms,
            steady_reduction_db=float(erle), first_second_reduction_db=float(startup),
            passed=bool(erle>=15), **timing))
        human = near.copy()
        human[:96000] = 0  # six seconds echo adaptation, then genuine double-talk
        mixed = human+echo
        out, timing = process(mixed, far)
        measured = compare(human, mixed, out, 112000, 352000)
        opening = compare(human, mixed, out, 96000, 112000)
        rows.append(dict(case="double_talk", delay_ms=delay_ms, **measured,
            opening_second=opening,
            passed=bool(measured["near_correlation"]>=.85 and
                        measured["near_reconstruction_improvement_db"]>=6 and
                        opening["near_correlation"]>=.85 and
                        measured["diagnostic_alignment_samples"]==0 and
                        opening["diagnostic_alignment_samples"]==0), **timing))
        missing = range(800, 810)
        out, timing = process(mixed, far, missing)
        exact = np.array_equal(out[128000:129600], mixed[128000:129600])
        rows.append(dict(case="reference_gap", delay_ms=delay_ms,
            missing_reference_exact_passthrough=bool(exact), passed=bool(exact), **timing))
    return dict(scope="controlled mixture of recorded speech; not live/browser/UID qualification",
        library_sha256=sha(args.library), capture_sha256=sha(args.capture),
        backend_library_sha256=sha(args.backend_library),
        render_sha256=sha(args.render), backend_revision="2cec2f52e26646f93bd2d5498bbabf59cba18da9",
        sample_rate=16000, frame_samples=160, seconds_per_case=24,
        passed=all(r["passed"] for r in rows), cases=rows)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--library", type=Path, required=True)
    p.add_argument("--backend-library", type=Path, required=True)
    p.add_argument("--capture", type=Path, required=True)
    p.add_argument("--render", type=Path, required=True)
    p.add_argument("--result", type=Path, required=True)
    args = p.parse_args()
    # Immutable evidence: never overwrite a previous result, including failure.
    with args.result.open("x") as report:
        try:
            result = run(args)
        except Exception as error:
            result = {"passed": False, "error": str(error)}
        json.dump(result, report, indent=2, allow_nan=False)
        report.write("\n")
    print(json.dumps(result, allow_nan=False))
    raise SystemExit(0 if result["passed"] else 1)
