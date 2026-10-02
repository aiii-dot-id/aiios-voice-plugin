"""Real worker/engine with deterministic PCM and one delayed private reply.

Only one acknowledgement is held outside the writer. Other replies and
events remain writable, so the test does not manufacture a congested pipe.
Model production, audio, the actual engine control owner and SDK remain real.
This module is diagnostic-only and not part of a packaged voice engine.
"""

import asyncio
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np

from runtime.plugin_engine.worker import serve
from tests.plugin_models import Models


class DelayedReply:
    def __init__(self, target):
        self.target = target
        self.delayed = False
        self.lock = threading.Lock()
        self.timer = None
        self.error = None

    def forward(self, data):
        with self.lock:
            result = self.target.write(data)
            self.target.flush()
            return result

    def release(self, data):
        try:
            self.forward(data)
            print("fixture synthesis reply released", file=sys.stderr, flush=True)
        except OSError as error:
            self.error = error

    def write(self, data):
        body = json.loads(bytes(data))
        if body.get("id") == 2 and "result" in body and not self.delayed:
            self.delayed = True
            print("fixture synthesis reply held", file=sys.stderr, flush=True)
            delay_ms = int(os.environ.get("AII_TEST_ACK_DELAY_MS", "750"))
            if delay_ms not in (0, 750):
                raise ValueError("fixture delay must be 0 or 750 ms")
            if delay_ms:
                self.timer = threading.Timer(delay_ms / 1000, self.release, (bytes(data),))
                self.timer.start()
                return len(data)
        return self.forward(data)

    def flush(self):
        with self.lock:
            self.target.flush()


def main():
    models = Models()
    cancelled = threading.Event()

    def produce(text):
        for _ in range(100):
            if cancelled.wait(0.01):
                return
            yield np.full(240, 0.2, np.float32), 24000, 1

    models.tts_stream = produce
    models.cancel_synthesis = cancelled.set
    writer = DelayedReply(sys.stdout.buffer)
    sys.stdout = SimpleNamespace(buffer=writer)
    try:
        with ThreadPoolExecutor(1) as model, ThreadPoolExecutor(1) as control:
            asyncio.run(serve(models, model, control))
    finally:
        if writer.timer is not None:
            writer.timer.join(timeout=2)
            if writer.timer.is_alive():
                raise RuntimeError("delayed reply timer did not retire")
        if writer.error is not None:
            raise writer.error


if __name__ == "__main__":
    main()
