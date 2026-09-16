"""Source-bound Windows speech engine and standalone app, with owned cleanup."""

import argparse
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web

from runtime.cuda_voice.session import CUDASession
from runtime.voice_app.server import create_app as application
from scripts.run_voice_platform import check_port
from scripts.serve_voice_reference import create_app as engine
from scripts.serve_voice_reference import source_identity

from .backend import WindowsModels
from .native import NativeBridge, binding, inventory


def model_loader(args, root, endpoint):
    """Explicit selection; an unavailable backend never silently substitutes."""
    backend = getattr(args, "tts_backend", "qwen")
    if backend == "pocket":
        from .pocket import WindowsPocketModels

        return partial(
            WindowsPocketModels,
            root,
            args.stage,
            args.output,
            endpoint,
            pocket_root=args.stage / "pocket-r1",
        )
    if backend != "qwen":
        raise ValueError("Unknown Windows TTS backend")
    return partial(
        WindowsModels,
        root,
        args.stage,
        args.output,
        endpoint,
        args.export_sha,
        predictor_graph=not args.eager_predictor,
    )


async def run(args):
    root = Path(__file__).resolve().parents[2]
    for port in (args.engine_port, args.app_port):
        check_port(port)
    if args.engine_port == args.app_port:
        raise ValueError("Different app and engine ports required")
    args.output.mkdir(parents=True, exist_ok=False)
    tts = ThreadPoolExecutor(max_workers=1, thread_name_prefix="windows-tts")
    endpoint = ThreadPoolExecutor(max_workers=1, thread_name_prefix="windows-pause")
    models, runners = None, []
    try:
        models = await asyncio.get_running_loop().run_in_executor(
            tts,
            model_loader(args, root, endpoint),
        )
        _, source_sha = source_identity()
        native = (
            json.loads(args.native_config.read_text()) if args.native_config else None
        )
        expected = binding(root, native) if native else None
        if native:
            await inventory(native)
        config = {
            "port": args.app_port,
            "voice_url": f"http://127.0.0.1:{args.engine_port}",
            "voice_source_sha256": source_sha,
            "evidence_root": str(args.output / "application-sessions"),
            "chat": {
                "url": "http://127.0.0.1:18081/v1/chat/completions",
                "model": "GLM-5.3-Ember-MLX-Q8-MTP",
                "timeout_seconds": 24,
                "max_tokens": 192,
            },
        }
        (args.output / "config.json").write_text(json.dumps(config, indent=2) + "\n")
        for app, port in (
            (
                engine(
                    models,
                    tts,
                    args.output / "engine-sessions",
                    args.engine_port,
                    session_factory=CUDASession,
                    native_factory=partial(NativeBridge, root, expected)
                    if native
                    else None,
                    native_identity=expected,
                    native_inventory=partial(inventory, native),
                ),
                args.engine_port,
            ),
            (application(config), args.app_port),
        ):
            runner = web.AppRunner(app)
            runners.append(runner)
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", port).start()
        print(
            json.dumps(
                {
                    "status": "ready",
                    "app_port": args.app_port,
                    "source_sha256": source_sha,
                    "capture_started": False,
                }
            ),
            flush=True,
        )
        if args.prove:
            from scripts.prove_voice_application import run as prove

            result = await prove(
                SimpleNamespace(
                    url=f"http://127.0.0.1:{args.app_port}",
                    output=args.output / "application-proof",
                    manual=True,
                )
            )
            if result:
                raise RuntimeError(
                    "Windows application proof failed; evidence retained"
                )
        else:
            # The instance owner requests shutdown, not a model or browser message.
            while not (args.output / "shutdown.request").exists():
                await asyncio.sleep(0.2)
    finally:
        for runner in reversed(runners):
            await runner.cleanup()
        if models is not None:
            await asyncio.get_running_loop().run_in_executor(tts, models.close)
            (args.output / "cleanup.json").write_text(
                json.dumps(
                    {
                        "recognizer_reaped": models.recognizer.child.poll() is not None,
                        "recognizer_ipc_joined": not models.recognizer.reader.is_alive()
                        and not models.recognizer.writer.is_alive(),
                        "http_runners_closed": len(runners),
                        "audio_defaults_changed": False,
                    },
                    indent=2,
                )
                + "\n"
            )
        endpoint.shutdown(wait=True)
        tts.shutdown(wait=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--stage", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--export-sha", required=True)
    p.add_argument("--engine-port", type=int, default=9148)
    p.add_argument("--app-port", type=int, default=9149)
    p.add_argument("--prove", action="store_true")
    p.add_argument("--native-config", type=Path)
    p.add_argument("--tts-backend", choices=("qwen", "pocket"), default="qwen")
    p.add_argument(
        "--eager-predictor",
        action="store_true",
        help="Explicit original-generator control path for comparison",
    )
    args = p.parse_args()
    if any(not 1024 <= p <= 65535 for p in (args.engine_port, args.app_port)):
        p.error("Unprivileged ports required")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
