"""Deterministic engine only for real SDK transport tests; never packaged."""

import asyncio
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from runtime.plugin_engine.worker import serve
from tests.plugin_models import Models


def main():
    child = None
    if os.environ.get("AII_TEST_GRANDCHILD") == "1":
        child = subprocess.Popen(
            [sys._base_executable, "-c", "import time; time.sleep(60)"]
        )
        print(
            json.dumps({"fixture_processes": [os.getpid(), child.pid]}),
            file=sys.stderr,
            flush=True,
        )
    if os.environ.get("AII_TEST_BLOCK_STARTUP") == "1":
        threading.Event().wait(60)
        raise RuntimeError("fixture escaped startup cleanup")
    with ThreadPoolExecutor(1) as model, ThreadPoolExecutor(1) as control:
        models = Models()
        ordinary = models.tts_stream
        cancelled = threading.Event()

        def cancellable(text):
            # The fixture must still be in flight when the host cancels.
            # A two-chunk instantaneous fake can finish between Windows
            # RPCs and cannot prove the cancellation path it is testing.
            if text.startswith("This is a longer reply.") and not cancelled.wait(10):
                raise RuntimeError("fixture cancellation never arrived")
            yield from ordinary(text)

        models.tts_stream = cancellable
        models.cancel_synthesis = cancelled.set
        if os.environ.get("AII_TEST_BLOCK_MODEL") == "1":

            def blocked(text):
                print("fixture inference blocked", file=sys.stderr, flush=True)
                threading.Event().wait(60)
                raise RuntimeError("fixture escaped forced cleanup")

            models.tts_stream = blocked
        asyncio.run(serve(models, model, control))
    if child is not None:
        child.terminate()
        child.wait(timeout=3)


if __name__ == "__main__":
    main()
