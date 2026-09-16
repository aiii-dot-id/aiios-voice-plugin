"""Real model worker with only the first synthesis acknowledgement held.

Diagnostic fixture: no replacement of inference, control, audio, or events.
"""

import asyncio
import json
import os
import sys
import threading
import time
from types import SimpleNamespace
from pathlib import Path

from tests.cpu_torch_fixture import install

install()

from runtime.plugin_engine import worker
from tests.plugin_delayed_ack_fixture import DelayedReply

original_serve = worker.serve


class FenceHeldReply(DelayedReply):
    """Hold the actual synthesis result through a matching real fence.

    The old ID-2 / fixed-delay fixture could release before Windows Web Audio
    began playback. Correlate by the engine's synthesis result instead, keep
    other output writable, and require the real fence within ten seconds.
    The minimum 750 ms hold is unchanged; browser startup cannot end it early.
    """

    def __init__(self, target, *, minimum=0.750, timeout=10):
        super().__init__(target)
        self.minimum, self.timeout = minimum, timeout
        self.fenced = threading.Event()
        self.synthesis_id = None
        self.held_at = None

    def release_when_fenced(self, data):
        if not self.fenced.wait(self.timeout):
            self.error = RuntimeError("fixture never observed the held synthesis fence")
            # Retire the fixture without manufacturing a successful fence.
            self.release(data)
            return
        time.sleep(max(0, self.minimum - (time.monotonic() - self.held_at)))
        self.release(data)

    def write(self, data):
        body = json.loads(bytes(data))
        result = body.get("result", {})
        if (not self.delayed and result.get("accepted") is True
                and result.get("synthesis_id") and result.get("output_stream")
                and "output_fenced" not in result):
            self.delayed = True
            self.synthesis_id = result["synthesis_id"]
            self.held_at = time.monotonic()
            print("fixture synthesis reply held until matching fence", file=sys.stderr, flush=True)
            self.timer = threading.Timer(0, self.release_when_fenced, (bytes(data),))
            self.timer.start()
            return len(data)
        written = self.forward(data)
        if (result.get("output_fenced") is True
                and result.get("synthesis_id") == self.synthesis_id):
            # The fence's own result is already forwarded before releasing
            # the older synthesis acknowledgement. No inference waits here.
            self.fenced.set()
        return written


async def delayed_serve(*args, **kwargs):
    if path := os.environ.get("AII_TEST_MODEL_EVIDENCE"):
        # Capture the actual already-loaded owner, not an expected profile.
        # This diagnostic file is not part of the public control vocabulary.
        with Path(path).open("x", encoding="utf8") as evidence:
            json.dump(args[0].identity, evidence, indent=2)
    writer = FenceHeldReply(sys.stdout.buffer)
    sys.stdout = SimpleNamespace(buffer=writer)
    try:
        return await original_serve(*args, **kwargs)
    finally:
        if writer.timer is not None:
            await asyncio.to_thread(writer.timer.join, 2)
            if writer.timer.is_alive():
                raise RuntimeError("delayed acknowledgement did not retire")
        if writer.error is not None:
            raise writer.error


if __name__ == "__main__":
    worker.serve = delayed_serve
    worker.main()
