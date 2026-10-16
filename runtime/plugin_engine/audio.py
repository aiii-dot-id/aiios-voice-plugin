"""Python boundary for the SDK AUD1 audio pair, not another public audio wire.

Held to the pinned SDK's vectors/audio_framing.json. Control stays on the Go
SDK lane; these descriptors carry only PCM and explicit discontinuity/end.
"""

import struct
from dataclasses import dataclass

HEADER = struct.Struct(">4sB3xIIqI")
MAX_PAYLOAD = 65536
PCM, DISCONTINUITY, END = 1, 2, 3


@dataclass(frozen=True)
class Frame:
    kind: int
    stream: int
    seq: int
    start: int
    pcm: bytes = b""

    def validate(self):
        if self.kind not in (PCM, DISCONTINUITY, END):
            raise ValueError("unknown SDK audio kind")
        for value in (self.stream, self.seq):
            if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
                raise ValueError("invalid SDK stream/sequence")
        if type(self.start) is not int or not 0 <= self.start <= 0x7FFFFFFFFFFFFFFF:
            raise ValueError("invalid SDK sample start")
        if len(self.pcm) > MAX_PAYLOAD or len(self.pcm) % 2:
            raise ValueError("invalid SDK PCM size/alignment")
        if self.kind != PCM and self.pcm:
            raise ValueError("non-PCM frame cannot carry samples")

    def encode(self):
        self.validate()
        return (
            HEADER.pack(
                b"AUD1", self.kind, self.stream, self.seq, self.start, len(self.pcm)
            )
            + self.pcm
        )


def exact(reader, count, *, boundary=False):
    chunks = bytearray()
    while len(chunks) < count:
        chunk = reader.read(count - len(chunks))
        if not chunk:
            if boundary and not chunks:
                return None
            raise EOFError("truncated SDK audio frame")
        chunks.extend(chunk)
    return bytes(chunks)


def read_frame(reader):
    header = exact(reader, HEADER.size, boundary=True)
    if header is None:
        return None
    magic, kind, stream, seq, start, size = HEADER.unpack(header)
    if magic != b"AUD1" or header[5:8] != b"\0\0\0":
        raise ValueError("invalid SDK audio header")
    if kind not in (PCM, DISCONTINUITY, END) or size > MAX_PAYLOAD:
        raise ValueError("invalid SDK audio kind/size")
    if size % 2 or (kind != PCM and size):
        raise ValueError("invalid SDK audio payload")
    frame = Frame(kind, stream, seq, start, exact(reader, size))
    frame.validate()
    return frame
