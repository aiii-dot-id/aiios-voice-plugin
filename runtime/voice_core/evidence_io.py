"""Append-only event evidence and bounded-memory finalization."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os


async def finalize_off_loop(function, *args):
    """Keep socket liveness during large evidence writes; don't abandon on cancel."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


def file_identity(path):
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    return {"bytes": path.stat().st_size, "sha256": digest}


class EventSpool:
    """A replayable JSONL journal; no growing in-memory event collection."""

    max_line_bytes = 1024 * 1024

    def __init__(self, path):
        self.path = path
        self.handle = path.open("x", encoding="utf-8", buffering=1)
        self.count = 0

    def __len__(self):
        return self.count

    def append(self, event):
        line = json.dumps(event, separators=(",", ":"), allow_nan=False)
        if len(line.encode("utf-8")) + 1 > self.max_line_bytes:
            raise ValueError("event exceeded journal line bound")
        self.handle.write(line + "\n")
        self.count += 1

    def lines(self):
        if not self.handle.closed:
            self.handle.flush()
        with self.path.open("r", encoding="utf-8") as source:
            while line := source.readline(self.max_line_bytes):
                if not line.endswith("\n"):
                    raise ValueError("partial or oversized journal event")
                yield line

    def __iter__(self):
        return (json.loads(line) for line in self.lines())

    def close(self):
        if not self.handle.closed:
            self.handle.flush()
            os.fsync(self.handle.fileno())
            self.handle.close()

    def export(self, destination, metadata=None):
        """Preserve the existing JSON artifact shape without materializing it."""
        with destination.open("x", encoding="utf-8") as output:
            if metadata is not None:
                if "events" in metadata:
                    raise ValueError("events belong to the journal, not metadata")
                prefix = json.dumps(metadata, allow_nan=False, sort_keys=True)
                output.write(prefix[:-1] + ("," if metadata else "") + '"events":')
            output.write("[")
            for index, line in enumerate(self.lines()):
                output.write(("," if index else "") + line.rstrip("\n"))
            output.write("]" + ("}" if metadata is not None else "") + "\n")
