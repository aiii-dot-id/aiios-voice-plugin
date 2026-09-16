"""Development alternatives to short-window external AEC delay resets.

These change only delay estimation. Neither candidate classifies speech or
suppresses VAD events. The original Mac frontend remains untouched.
"""

from collections import deque

from .echo import RenderDelay


class NativeDelay:
    """Delegate acoustic-delay estimation to AEC3 itself; no external resets."""

    hint_ms = 0
    correlation = 0.0
    updates = 0

    def observe(self, near, far):
        return 0


class LongerEvidenceDelay(RenderDelay):
    """Same estimator but 512 ms of acoustic evidence instead of 128 ms."""

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
