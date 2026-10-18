"""Speak application text to a streamed WAV; Ctrl-C cancels without false success."""

import argparse
import asyncio
import json
import signal
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from .mlx_backend import MLXTTSBackend
from .service import SpeechOutput


async def run(args):
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise ValueError("refusing to overwrite output")
    with ThreadPoolExecutor(max_workers=1) as executor:
        loop = asyncio.get_running_loop()
        backend = await loop.run_in_executor(executor, MLXTTSBackend, args.root)
        service = SpeechOutput(backend, executor)
        job = service.submit(args.text)
        loop.add_signal_handler(signal.SIGINT, service.cancel, job)
        try:
            with args.output.open("xb") as raw, wave.open(raw, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                while (chunk := await job.read()) is not None:
                    pcm = np.rint(np.clip(chunk.samples, -1, 1) * 32767).astype("<i2")
                    wav.writeframes(pcm.tobytes())
            result = await job.wait()
        finally:
            loop.remove_signal_handler(signal.SIGINT)
            await service.close(abort=True)
        print(
            json.dumps(
                {
                    "output": str(args.output.resolve()),
                    "model": backend.identity,
                    **result,
                }
            )
        )
        if result["state"] != "completed":
            raise RuntimeError("output is partial: synthesis was not completed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--text", required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
