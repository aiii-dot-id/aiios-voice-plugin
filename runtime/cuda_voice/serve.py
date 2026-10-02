"""Loopback-only CUDA service using the existing voice HTTP/session surface."""

import argparse
import asyncio
import json
import signal
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

from aiohttp import web

from scripts.serve_voice_reference import create_app

from .backend import CUDAModels
from .native import NativeBridge, binding, inventory
from .session import CUDASession


async def run(args):
    root = Path(__file__).resolve().parents[2]
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cuda-tts")
    endpoint = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cuda-semantic")
    models = runner = None
    try:
        models = await asyncio.get_running_loop().run_in_executor(
            executor, partial(CUDAModels, root, args.stage, args.output, endpoint)
        )
        native = (
            json.loads(args.native_config.read_text()) if args.native_config else None
        )
        expected = binding(root, native) if native else None
        if native:
            await inventory(native)
        app = create_app(
            models,
            executor,
            args.output,
            args.port,
            session_factory=CUDASession,
            native_factory=partial(NativeBridge, root, expected) if native else None,
            native_identity=expected,
            native_inventory=partial(inventory, native) if native else None,
        )
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", args.port).start()
        print(
            json.dumps({"status": "ready", "port": args.port, "backend": "cuda"}),
            flush=True,
        )
        stopped = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
        await stopped.wait()
    finally:
        if runner is not None:
            await runner.cleanup()
        if models is not None:
            await asyncio.get_running_loop().run_in_executor(executor, models.close)
        endpoint.shutdown(wait=True)
        executor.shutdown(wait=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=9146)
    parser.add_argument("--native-config", type=Path)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("invalid port")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
