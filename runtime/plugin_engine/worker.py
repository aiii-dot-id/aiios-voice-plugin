import argparse
import asyncio
import contextlib
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .audio import PCM, read_frame
from .session import ResidentEngine
from .settings import SettingsReader

MAX_LINE = 1024 * 1024


def watch_carrier_liveness():
    if os.name == "nt":
        return
    value = os.environ.get("AII_VOICE_CARRIER_LIVENESS_FD")
    if value is None:
        return
    if not value.isdecimal() or not 3 <= int(value) <= 1024:
        raise ValueError("invalid carrier liveness descriptor")
    fd = int(value)

    def watch():
        try:
            code = 74 if not os.read(fd, 1) else 75
        except OSError:
            code = 75
        os._exit(code)

    threading.Thread(target=watch, name="voice-carrier-liveness", daemon=True).start()


def native_candidate_options(args):
    root = getattr(args, "native_stt_root", None)
    python = getattr(args, "native_stt_python", None)
    if root is None and python is None:
        return {}
    if root is None or python is None:
        raise ValueError("native candidate requires both root and interpreter")
    if args.backend != "windows-pocket":
        raise ValueError("native candidate is qualified only with Windows Pocket")
    if any(
        getattr(args, k, None)
        for k in ("runtime_root", "model_assets", "packaged_runtime", "fixture")
    ):
        raise ValueError("native candidate cannot override packaged or fixture models")
    return {"native_stt_root": root, "native_stt_python": python}


def load_models(args, endpoint):
    native_args = native_candidate_options(args)
    assets = None
    if getattr(args, "model_assets", None) is not None:
        if args.backend not in ("mlx", "windows-pocket", "cuda"):
            raise ValueError(
                "installed model layout has not been qualified for this backend"
            )
        from runtime.model_assets import ModelAssets

        assets = ModelAssets(args.model_assets, args.root, backend=args.backend)
    asset_args = {"assets": assets} if assets is not None else {}
    if args.backend == "mlx":
        from .mlx import ResidentMLXModels

        runtime_root = getattr(args, "runtime_root", None)
        if assets is not None:
            runtime_root = assets.catalog.parent.parent.parent
        voice_args = (
            {"voice_catalog": runtime_root / "resources/voices/catalog.json"}
            if runtime_root is not None
            else {}
        )
        return ResidentMLXModels(
            args.root,
            stt_right_context=6,
            semantic_pause=True,
            endpoint_executor=endpoint,
            **asset_args,
            **voice_args,
        )
    if args.stage is None:
        raise ValueError("CUDA/Windows require explicit --stage")
    if args.backend == "windows-pocket":
        from runtime.native_pocket.profile import selection

        if selection(getattr(args, "runtime_root", None)) is not None:
            from runtime.windows_voice.native_pocket import WindowsNativePocketModels

            return WindowsNativePocketModels(
                args.root, endpoint, runtime_root=args.runtime_root, assets=assets
            )
        if args.pocket_root is None:
            raise ValueError("Windows Pocket requires --pocket-root")
        from runtime.windows_voice.pocket import WindowsPocketModels

        return WindowsPocketModels(
            args.root,
            args.stage,
            args.state_dir,
            endpoint,
            pocket_root=args.pocket_root,
            runtime_root=getattr(args, "runtime_root", None),
            record_observations=False,
            **native_args,
            **asset_args,
        )
    if args.backend == "cuda":
        from runtime.cuda_voice.backend import CUDAModels

        return CUDAModels(
            args.root,
            args.stage,
            args.state_dir,
            endpoint,
            record_observations=False,
            runtime_root=getattr(args, "runtime_root", None),
            **asset_args,
        )
    raise ValueError("unsupported backend")


def inherited(name, mode):
    value = int(os.environ[name])
    if value < 0:
        raise ValueError("invalid inherited audio descriptor")
    if os.name == "nt":
        import msvcrt

        value = msvcrt.open_osfhandle(
            value, os.O_BINARY | (os.O_RDONLY if mode == "rb" else os.O_WRONLY)
        )
    return os.fdopen(value, mode, buffering=0)


async def serve(
    models, model_executor, control_executor, readiness=None, *, speaker_tools=None
):
    loop = asyncio.get_running_loop()
    wire_stdout = sys.stdout.buffer
    sys.stdout = sys.stderr
    audio_in = inherited("AII_AUDIO_IN_FD", "rb")
    audio_out = inherited("AII_AUDIO_OUT_FD", "wb")
    messages = asyncio.Queue(maxsize=64)
    incoming = asyncio.Queue(maxsize=64)
    io_executor = ThreadPoolExecutor(1, thread_name_prefix="sdk-audio-output")
    message_executor = ThreadPoolExecutor(1, thread_name_prefix="sdk-private-events")
    stopped = threading.Event()

    def emit(message):
        messages.put_nowait(message)

    def write_all(handle, data):
        view = memoryview(data)
        while view:
            n = handle.write(view)
            if not n:
                raise OSError("zero-length write")
            view = view[n:]

    def write_message(data):
        write_all(wire_stdout, data)
        wire_stdout.flush()

    def audio_write(frame, generation):
        if frame.kind == PCM and generation.fenced:
            return False
        write_all(audio_out, frame.encode())
        return True

    async def write_audio(frame, generation):
        async with asyncio.timeout(3):
            return await loop.run_in_executor(
                io_executor, audio_write, frame, generation
            )

    async def retire_audio():
        await loop.run_in_executor(io_executor, lambda: None)

    settings = SettingsReader(emit)
    engine = ResidentEngine(
        models,
        model_executor,
        control_executor,
        lambda event: emit({"event": event}),
        write_audio,
        retire_audio=retire_audio,
        speaker_tools=speaker_tools,
        speaker_observations=speaker_tools is not None,
        settings_loader=settings.load
        if hasattr(models, "bind_operator_settings")
        else None,
    )

    async def writer():
        while True:
            body = await messages.get()
            data = (
                json.dumps(body, allow_nan=False, separators=(",", ":")).encode()
                + b"\n"
            )
            if len(data) > MAX_LINE:
                raise RuntimeError("private event exceeds bound")
            async with asyncio.timeout(3):
                await loop.run_in_executor(message_executor, write_message, data)

    def put(item):
        if stopped.is_set():
            return
        future = asyncio.run_coroutine_threadsafe(incoming.put(item), loop)
        try:
            future.result(timeout=3)
        except (TimeoutError, RuntimeError):
            future.cancel()
            loop.call_soon_threadsafe(
                engine.fail, RuntimeError("private ingress saturated")
            )

    def controls():
        try:
            while not stopped.is_set():
                line = sys.stdin.buffer.readline(MAX_LINE + 1)
                if not line:
                    watchdog = threading.Timer(6, lambda: os._exit(72))
                    watchdog.daemon = True
                    watchdog.start()
                    put(("eof", None))
                    return
                if len(line) > MAX_LINE or not line.endswith(b"\n"):
                    raise ValueError("invalid private control line")
                put(("control", json.loads(line)))
        except (OSError, ValueError, RuntimeError) as error:
            put(("fault", str(error)))

    def audio_reader():
        try:
            while not stopped.is_set():
                frame = read_frame(audio_in)
                if frame is None:
                    put(("audio_eof", None))
                    return
                put(("audio", frame))
        except (OSError, EOFError, ValueError, RuntimeError) as error:
            put(("fault", str(error)))

    emit(
        {
            "ready": {
                "identity": getattr(models, "identity", {}),
                "readiness": readiness,
                "audio_devices_owned": False,
                "playback_report_control_implemented": True,
                "host_playback_binding_qualified": False,
            }
        }
    )
    wt = asyncio.create_task(writer())
    for target in (controls, audio_reader):
        threading.Thread(target=target, daemon=True).start()
    propagating = False
    try:
        while True:
            item = asyncio.create_task(incoming.get())
            done, _ = await asyncio.wait(
                (item, wt), return_when=asyncio.FIRST_COMPLETED
            )
            if wt in done:
                item.cancel()
                await wt
            kind, body = await item
            if kind == "eof":
                return
            if kind == "audio_eof":
                if engine.id and engine.lifecycle not in ("closed", "failed"):
                    engine.fail(
                        RuntimeError("audio endpoint lost; not graceful finish")
                    )
                continue
            if kind == "fault":
                raise RuntimeError(body)
            if kind == "audio":
                try:
                    engine.feed(body)
                except (ValueError, RuntimeError) as error:
                    engine.fail(error)
                continue
            if isinstance(body, dict) and "settings_reply" in body:
                if set(body) != {"settings_reply"}:
                    raise ValueError("ambiguous private settings reply")
                settings.receive(body["settings_reply"])
                continue
            if not isinstance(body, dict) or type(body.get("id")) is not int:
                raise ValueError("malformed private request")
            try:
                result = engine.admit(body["operation"], body["arguments"])
                emit({"id": body["id"], "result": result})
            except (ValueError, RuntimeError) as error:
                emit({"id": body["id"], "error": str(error)})
    except BaseException:
        propagating = True
        raise
    finally:
        stopped.set()
        cleanup_errors = await engine.shutdown()
        wt.cancel()
        await asyncio.gather(wt, return_exceptions=True)
        io_executor.shutdown(wait=False, cancel_futures=True)
        message_executor.shutdown(wait=False, cancel_futures=True)
        audio_in.close()
        audio_out.close()
        for error in cleanup_errors:
            print("worker cleanup:", error, file=sys.stderr)
        if cleanup_errors and not propagating:
            raise RuntimeError("worker cleanup failed: " + "; ".join(cleanup_errors))


def main():
    watch_carrier_liveness()
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--runtime-root", type=Path)
    p.add_argument("--model-assets", type=Path)
    p.add_argument(
        "--backend", choices=("mlx", "cuda", "windows-pocket"), default="mlx"
    )
    p.add_argument("--stage", type=Path)
    p.add_argument("--state-dir", type=Path)
    p.add_argument("--pocket-root", type=Path)
    p.add_argument("--native-stt-root", type=Path)
    p.add_argument("--native-stt-python", type=Path)
    args = p.parse_args()
    with (
        ThreadPoolExecutor(1, thread_name_prefix="voice-model") as executor,
        ThreadPoolExecutor(1, thread_name_prefix="voice-control") as control,
        ThreadPoolExecutor(1, thread_name_prefix="voice-endpoint") as endpoint,
    ):
        with contextlib.redirect_stdout(sys.stderr):
            models = executor.submit(load_models, args, endpoint).result()
        try:
            from .readiness import warm_models

            with contextlib.redirect_stdout(sys.stderr):
                readiness = executor.submit(warm_models, models, args.backend).result()
            asyncio.run(serve(models, executor, control, readiness))
        finally:
            close = getattr(models, "close", None)
            if close:
                executor.submit(close).result()


if __name__ == "__main__":
    main()
