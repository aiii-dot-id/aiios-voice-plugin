"""Experimental AEC adapter with explicit native latency and complete DSP tail.

The measured Linux AEC3 path delays audio by 128 samples. Pair delayed native
output with its original mic samples and original selection mask before any
raw/clean switch. This introduces real buffering, not a future-sample claim.
No live loader selects this adapter. Native acoustic quality remains unqualified.
"""

import numpy as np

FRAME = 160
PACKET = 512
LATENCY = 128


class LatencyAlignedEcho:
    def __init__(self, processor):
        self.processor = processor
        self.near = np.empty(0, np.float32)
        self.far = np.empty(0, np.float32)
        self.raw_wait = np.empty(0, np.float32)
        self.mask_wait = np.empty(0, bool)
        self.clean_wait = np.empty(0, np.float32)
        self.ready = np.empty(0, np.float32)
        self.skip = LATENCY
        self.received = self.paired = self.padding_samples = 0
        self.render_tail = 0
        self.ended = False

    def _frame(self, near, far, valid):
        clean = self.processor.process(near, far)
        if clean.shape != (FRAME,) or not np.isfinite(clean).all():
            raise RuntimeError("native echo returned invalid frame")
        if valid:
            self.render_tail = (
                16000
                if float(np.mean(far[:valid] ** 2)) > 1e-6
                else max(0, self.render_tail - valid)
            )
            self.raw_wait = np.r_[self.raw_wait, near[:valid]]
            self.mask_wait = np.r_[
                self.mask_wait, np.full(valid, bool(self.render_tail))
            ]
        skip = min(self.skip, len(clean))
        self.skip -= skip
        self.clean_wait = np.r_[self.clean_wait, clean[skip:]]
        count = min(len(self.raw_wait), len(self.clean_wait))
        self.ready = np.r_[
            self.ready,
            np.where(
                self.mask_wait[:count], self.clean_wait[:count], self.raw_wait[:count]
            ),
        ]
        self.raw_wait = self.raw_wait[count:].copy()
        self.mask_wait = self.mask_wait[count:].copy()
        self.clean_wait = self.clean_wait[count:].copy()
        self.paired += count

    def _take(self, final=False):
        count = len(self.ready) if final else len(self.ready) // PACKET * PACKET
        result = [
            self.ready[i : min(i + PACKET, count)].copy()
            for i in range(0, count, PACKET)
        ]
        self.ready = self.ready[count:].copy()
        return result

    def push(self, near, far):
        near, far = np.asarray(near, np.float32), np.asarray(far, np.float32)
        if self.ended:
            raise ValueError("echo input after finish")
        if (
            near.ndim != 1
            or near.shape != far.shape
            or not 0 < len(near) <= PACKET
            or not np.isfinite(near).all()
            or not np.isfinite(far).all()
        ):
            raise ValueError("bounded aligned finite mono input required")
        self.received += len(near)
        self.near, self.far = np.r_[self.near, near], np.r_[self.far, far]
        while len(self.near) >= FRAME:
            self._frame(self.near[:FRAME], self.far[:FRAME], FRAME)
            self.near, self.far = self.near[FRAME:].copy(), self.far[FRAME:].copy()
        return self._take()

    def finish(self):
        if self.ended:
            return []
        self.ended = True
        if len(self.near):
            valid = len(self.near)
            self.padding_samples += FRAME - valid
            self._frame(
                np.pad(self.near, (0, FRAME - valid)),
                np.pad(self.far, (0, FRAME - valid)),
                valid,
            )
            self.near = self.far = np.empty(0, np.float32)
        for _ in range((LATENCY + FRAME - 1) // FRAME + 1):
            if self.paired == self.received:
                break
            self._frame(np.zeros(FRAME, np.float32), np.zeros(FRAME, np.float32), 0)
            self.padding_samples += FRAME
        if self.paired != self.received or len(self.raw_wait):
            raise RuntimeError("native delayed tail did not drain")
        self.clean_wait = np.empty(0, np.float32)
        return self._take(final=True)
