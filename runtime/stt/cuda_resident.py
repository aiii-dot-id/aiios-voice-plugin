"""Experimental CUDA recognizer worker driven by real arriving PCM.

Bounded private development IPC, not the SDK's frozen resident transport.
The reader only admits PCM/controls. One inference owner touches model state.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from pathlib import Path

import numpy as np

STT_MANIFEST_SHA256 = "79136db6634bf183f19b903424a8a9e5456633223d8e75f580b7798c3d90f122"


def verify_bound_snapshot(root, *, model_assets=None, model_backend="windows-pocket"):
    """Run the existing complete verifier, never a cached integrity verdict."""
    if __package__:
        from scripts.verify_snapshot import file_sha256, verify
    else:
        from verify_snapshot import file_sha256, verify

    begun = time.perf_counter()
    if model_assets is not None:
        from runtime.model_assets import ModelAssets

        assets = ModelAssets(model_assets, root, backend=model_backend)
        if assets.manifest_sha("stt") != STT_MANIFEST_SHA256:
            raise ValueError("unbound STT model")
        return assets.snapshot("stt"), STT_MANIFEST_SHA256, time.perf_counter() - begun
    manifest = root / "artifacts/model-manifest.json"
    bound = file_sha256(manifest)
    if bound != STT_MANIFEST_SHA256:
        raise ValueError("unbound STT model")
    metadata = json.loads(manifest.read_text())
    snapshot = root / "artifacts/exact/snapshots" / metadata["source"]["revision"]
    result = verify(manifest, snapshot)
    if result["status"] != "passed" or result["manifest_sha256"] != bound:
        raise ValueError("STT snapshot failed verification")
    return snapshot, bound, time.perf_counter() - begun


@contextmanager
def snapshot_startup(
    root, *, overlap, model_assets=None, model_backend="windows-pocket"
):
    """Overlap integrity with imports only; join before any checkpoint load.

    The context owns its single worker even when importing the runtime fails.
    Hash failures propagate from finish; neither a timeout nor an incomplete
    future grants loading. Non-Windows callers retain serial verification.
    """
    owner = (
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt-integrity")
        if overlap
        else nullcontext()
    )
    with owner as pool:
        options = {"model_assets": model_assets} if model_assets is not None else {}
        if model_assets is not None and model_backend != "windows-pocket":
            options["model_backend"] = model_backend
        future = (
            pool.submit(verify_bound_snapshot, root, **options) if overlap else None
        )

        def finish():
            begun = time.perf_counter()
            snapshot, bound, duration = (
                future.result()
                if future is not None
                else verify_bound_snapshot(root, **options)
            )
            return (
                snapshot,
                bound,
                {
                    "snapshot_verification": duration,
                    "snapshot_verification_wait": time.perf_counter() - begun,
                    "snapshot_verification_overlapped": overlap,
                },
            )

        yield finish


def configure_memory_budget(torch, budget_mib=None):
    """Retain the Linux default; permit a measured, bounded target-specific cap."""
    if budget_mib is None:
        torch.cuda.set_per_process_memory_fraction(0.24, 0)
        return
    if type(budget_mib) is not int or budget_mib < 512:
        raise ValueError("CUDA memory budget must be an integer >=512 MiB")
    free, total = torch.cuda.mem_get_info()
    budget = budget_mib * 1024**2
    if budget + 128 * 1024**2 > free or budget > total:
        raise RuntimeError(
            "CUDA budget plus 128 MiB reserve exceeds current free memory"
        )
    torch.cuda.set_per_process_memory_fraction(budget / total, 0)


def windows_available_memory():
    """Read physical headroom without changing a device or system policy."""
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong)
            for name in (
                "total_physical",
                "available_physical",
                "total_pagefile",
                "available_pagefile",
                "total_virtual",
                "available_virtual",
                "available_extended_virtual",
            )
        ]

    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError("cannot read physical memory headroom")
    return status.available_physical


def transfer_to_cuda(torch, model, *, platform=None, memory_reader=None):
    """Stage the bounded Windows checkpoint, preserving the other CUDA path.

    The Windows A-B-B-A diagnostic halved this checkpoint's transfer cost only
    when CPU page-locking preceded asynchronous copies. Count staging in the
    caller's transfer phase. Low/unknown host headroom keeps the original CUDA
    loading path with an explicit reason; it is not an inference CPU fallback.
    Any staging/copy failure propagates and cannot publish model readiness.
    """
    platform = sys.platform if platform is None else platform
    info = {"mode": "pageable", "reason": "unchanged_platform_path", "bytes": 0}
    if platform == "win32":
        size = sum(t.numel() * t.element_size() for t in model.parameters())
        size += sum(t.numel() * t.element_size() for t in model.buffers())
        info["reason"] = "checkpoint_exceeds_staging_bound"
        if size <= 3072 * 1024**2:
            try:
                free = (memory_reader or windows_available_memory)()
            except OSError:
                free = None
            info["reason"] = (
                "physical_memory_unavailable" if free is None else "low_host_headroom"
            )
            if free is not None and free >= 8 * 1024**3:
                model._apply(lambda t: t.pin_memory())
                if any(
                    not t.is_pinned() for t in (*model.parameters(), *model.buffers())
                ):
                    raise RuntimeError("checkpoint staging incomplete")
                info = {
                    "mode": "pinned",
                    "reason": "bounded_windows_staging",
                    "bytes": size,
                }
    model = (
        model.to("cuda:0", non_blocking=True)
        if info["mode"] == "pinned"
        else model.to("cuda:0")
    )
    torch.cuda.synchronize()
    return model, info


def recognition_terminal(
    stream, sid, text, ids, *, features_exhausted, max_new_tokens, failure=None
):
    """Report completion facts separately from decoded text (including no text).

    The pinned encoder-decoder prepends one decoder-start token. Exhaustion is
    observed at the actual feature iterator, not inferred from a padded span.
    A no-text final is transport success, not an acoustic-silence assertion.
    """
    covered = stream.spans[-1]["source_end"] if stream.spans else None
    generated = max(0, len(ids) - 1)
    facts = {
        "input_finished": stream.finished,
        "features_exhausted": features_exhausted,
        "covered_source_end": covered,
        "token_count": len(ids),
        "generated_token_count": generated,
        "max_new_tokens": max_new_tokens,
        "token_limit_reached": generated >= max_new_tokens,
    }
    faults = []
    if failure is not None:
        faults.append("inference_failure")
    if stream.samples <= 0:
        faults.append("no_input")
    if not stream.finished:
        faults.append("input_not_finished")
    if not stream.spans:
        faults.append("no_acoustic_spans")
    elif covered < stream.samples:
        faults.append("acoustic_tail_unconsumed")
    if not features_exhausted:
        faults.append("features_not_exhausted")
    if not ids:
        faults.append("missing_decoder_sequence")
    if facts["token_limit_reached"]:
        faults.append("token_limit_reached")
    if stream.cancelled.is_set() and failure is None:
        event, outcome = "cancelled", "cancelled"
    elif faults:
        event, outcome = "error", "failed"
    else:
        event, outcome = "final", "recognized" if text else "no_text"
    terminal = {
        "event": event,
        "sid": sid,
        "outcome": outcome,
        "text": text,
        "tokens": list(ids),
        "admitted": stream.samples,
        "spans": list(stream.spans),
        "completion": facts,
        "failure_codes": faults if event == "error" else [],
    }
    if failure is not None:
        terminal["message"] = failure
    return terminal


def main():
    startup_begin = time.perf_counter()
    startup = {}
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--max-new-tokens", type=int, default=2048)
    parser.add_argument("--max-input-seconds", type=int, default=20)
    parser.add_argument("--memory-budget-mib", type=int)
    parser.add_argument("--model-assets", type=Path)
    parser.add_argument(
        "--model-backend", choices=("windows-pocket", "cuda"), default="windows-pocket"
    )
    args = parser.parse_args()
    if not 1 <= args.max_new_tokens <= 2048:
        parser.error("max-new-tokens must be between 1 and 2048")
    if not 1 <= args.max_input_seconds <= 60:
        parser.error("max-input-seconds must be between 1 and 60")
    if not __package__:
        sys.path.insert(0, str(args.root))  # explicit laboratory script mode
    with snapshot_startup(
        args.root,
        overlap=sys.platform == "win32",
        model_assets=args.model_assets,
        model_backend=args.model_backend,
    ) as verified:
        import torch

        if __package__:
            from .cuda_stream import PCMIngress, StreamCancelled, feature_stream
        else:
            from cuda_stream import PCMIngress, StreamCancelled, feature_stream
        from transformers import (
            AutoModelForRNNT,
            AutoProcessor,
            StoppingCriteria,
            StoppingCriteriaList,
        )
        from transformers.generation.streamers import BaseStreamer

        startup["imports"] = time.perf_counter() - startup_begin
        torch.set_num_threads(4)
        phase_begin = time.perf_counter()
        configure_memory_budget(torch, args.memory_budget_mib)
        torch.backends.cuda.matmul.allow_tf32 = torch.backends.cudnn.allow_tf32 = False
        startup["cuda_initialization"] = time.perf_counter() - phase_begin
        snapshot, manifest_digest, verification_timing = verified()
        startup.update(verification_timing)

    writer = threading.Lock()

    def emit(event, **fields):
        with writer:
            print(
                "VF102 "
                + json.dumps(
                    {"event": event, "at_ns": time.monotonic_ns(), **fields},
                    allow_nan=False,
                ),
                flush=True,
            )

    phase_begin = time.perf_counter()
    processor = AutoProcessor.from_pretrained(
        snapshot, local_files_only=True, trust_remote_code=False
    )
    processor.set_num_lookahead_tokens(6)
    startup["processor_load"] = time.perf_counter() - phase_begin
    phase_begin = time.perf_counter()
    model = AutoModelForRNNT.from_pretrained(
        snapshot,
        dtype=torch.float32,
        local_files_only=True,
        trust_remote_code=False,
        attn_implementation="sdpa",
    ).eval()
    startup["checkpoint_load_cpu"] = time.perf_counter() - phase_begin
    phase_begin = time.perf_counter()
    model, transfer_staging = transfer_to_cuda(torch, model)
    startup["transfer_to_cuda"] = time.perf_counter() - phase_begin
    if any(
        p.device.type != "cuda" or p.dtype != torch.float32 for p in model.parameters()
    ):
        raise ValueError("STT neural residency differs")
    torch.cuda.empty_cache()  # Retain weights, release transient loader allocations.
    emit(
        "ready",
        parameters=sum(p.numel() for p in model.parameters()),
        model_manifest_sha256=manifest_digest,
        device=torch.cuda.get_device_name(),
        torch_version=torch.__version__,
        cuda_version=torch.version.cuda,
        model_dtype=str(model.dtype),
        memory_allocated_bytes=torch.cuda.memory_allocated(),
        memory_budget_mib=args.memory_budget_mib,
        max_input_seconds=args.max_input_seconds,
        transfer_staging=transfer_staging,
        startup_seconds=startup | {"total": time.perf_counter() - startup_begin},
    )
    worker, ingress = None, None
    idle = threading.Event()
    idle.set()
    seen = set()

    def run(stream, sid):
        tokens = []
        ids, text, exhausted = [], "", False

        def features():
            nonlocal exhausted
            yield from feature_stream(
                stream, processor, device=model.device, dtype=model.dtype
            )
            exhausted = True

        def terminal_result(failure=None):
            result = recognition_terminal(
                stream,
                sid,
                text,
                ids,
                features_exhausted=exhausted,
                max_new_tokens=args.max_new_tokens,
                failure=failure,
            )
            result["peak_allocated_bytes"] = torch.cuda.max_memory_allocated()
            return result

        class Cancel(StoppingCriteria):
            def __call__(self, input_ids, scores, **kwargs):
                return torch.full(
                    (input_ids.shape[0],),
                    stream.cancelled.is_set(),
                    dtype=torch.bool,
                    device=input_ids.device,
                )

        class Output(BaseStreamer):
            def put(self, values):
                tokens.extend(values.reshape(-1).tolist())
                emit(
                    "partial",
                    sid=sid,
                    text=processor.decode(tokens, skip_special_tokens=True),
                    admitted=stream.samples,
                )

            def end(self):
                pass

        def entered(*_):
            emit("encoder", sid=sid, admitted=stream.samples)

        def finite(module, inputs, output):
            if not torch.isfinite(output[0]).all():
                raise ValueError("nonfinite acoustic output")

        hooks = [
            model.get_encoder().register_forward_pre_hook(entered),
            model.get_encoder().register_forward_hook(finite),
        ]
        try:
            with torch.inference_mode():
                output = model.generate(
                    input_features=features(),
                    prompt_ids=torch.tensor([0], dtype=torch.long, device=model.device),
                    num_lookahead_tokens=6,
                    max_new_tokens=args.max_new_tokens,
                    do_sample=False,
                    return_dict_in_generate=True,
                    streamer=Output(),
                    stopping_criteria=StoppingCriteriaList([Cancel()]),
                )
            ids = output.sequences[0].tolist()
            text = processor.decode(ids, skip_special_tokens=True).strip()
            terminal = terminal_result()
        except StreamCancelled:
            terminal = terminal_result()
        except Exception as exc:  # noqa: BLE001 - deliver inference failure to owner
            terminal = terminal_result(repr(exc))
        finally:
            for hook in hooks:
                hook.remove()
        # No further model access after idle. A final event must authorize the
        # next start/close even while this thread is returning from emit().
        idle.set()
        emit(**terminal)

    try:
        while True:
            line = sys.stdin.buffer.readline(32769)
            if not line:
                break
            if len(line) > 32768:
                raise ValueError("oversized control frame")
            row = json.loads(line)
            op = row["op"]
            if op == "start":
                sid = row["sid"]
                if (
                    not isinstance(sid, str)
                    or not sid
                    or sid in seen
                    or not idle.is_set()
                ):
                    raise ValueError("duplicate or overlapping recognition")
                seen.add(sid)
                ingress = PCMIngress(capacity=16000 * args.max_input_seconds)
                idle.clear()
                worker = threading.Thread(target=run, args=(ingress, sid), daemon=True)
                worker.start()
                emit("opened", sid=sid)
            elif op == "close":
                if not idle.is_set():
                    raise ValueError("close while recognition unresolved")
                break
            else:
                if ingress is None or row.get("sid") != sid:
                    raise ValueError("control for unknown stream")
                if op == "pcm":
                    if row["start"] != ingress.samples:
                        raise ValueError("input sample gap")
                    values = np.frombuffer(
                        base64.b64decode(row["pcm"], validate=True), dtype="<f4"
                    )
                    if len(values) > 2048:
                        raise ValueError("input message exceeds bounded chunk")
                    ingress.push(values)
                elif op == "finish":
                    ingress.finish()
                    emit("input_finished", sid=sid, admitted=ingress.samples)
                elif op == "cancel":
                    ingress.cancel()
                    emit("cancel_admitted", sid=sid)
                else:
                    raise ValueError("unknown control")
    finally:
        if worker and worker.is_alive():
            ingress.cancel()
            worker.join(10)
            if worker.is_alive():
                raise RuntimeError("worker failed to exit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
