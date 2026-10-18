from collections import deque

from .echo import RenderDelay


class NativeDelay:
    hint_ms = 0
    correlation = 0.0
    updates = 0

    def observe(self, near, far):
        return 0


class LongerEvidenceDelay(RenderDelay):
    def __init__(self):
        super().__init__()
        self.near = deque(maxlen=16)
        self.far = deque(maxlen=48)


def configure(front, mode):
    if mode == "native":
        front.delay = NativeDelay()
    elif mode == "long-window":
        front.delay = LongerEvidenceDelay()
    elif mode != "existing":
        raise ValueError("unknown delay candidate")
    return front
