"""Pinned encoder tensors supplied through ORT's native initializer API.

Only the already-qualified checkpoint/graph pair is accepted. No path from
an ONNX external-data record is opened here: the runtime owns the one file
name and checks the complete bytes before exposing tensor views. The read-only
mapping lives until the owning recognizer has destroyed its sessions.
"""

import hashlib
import json
import mmap
import os
import stat
import struct

import numpy as np

from runtime.model_assets import checked, object_pairs, read_json
from runtime.stt.native_assets import CHECKPOINT_BYTES, CHECKPOINT_SHA, PINNED_FILES


class EncoderInitializers:
    def __init__(self, root):
        self.handle = self.mapping = None
        self.names, self.views, self.values = [], [], []
        try:
            index_path = checked(root, "weight-view.json")
            index, index_raw = read_json(index_path)
            if hashlib.sha256(index_raw).hexdigest() != PINNED_FILES["weight-view.json"][1]:
                raise ValueError("External initializer index binding differs")
            if (index["checkpoint_sha256"], index["checkpoint_bytes"]) != (CHECKPOINT_SHA, CHECKPOINT_BYTES):
                raise ValueError("External initializer checkpoint binding differs")
            graph = checked(root, "encoder.onnx")
            with graph.open("rb") as f:
                if os.fstat(f.fileno()).st_size != PINNED_FILES["encoder.onnx"][0] or hashlib.file_digest(f, "sha256").hexdigest() != PINNED_FILES["encoder.onnx"][1]:
                    raise ValueError("External initializer graph binding differs")
            path = checked(root, "model.safetensors")
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            self.handle = os.fdopen(os.open(path, flags), "rb")
            before = os.fstat(self.handle.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != CHECKPOINT_BYTES:
                raise ValueError("External initializer file extent/type differs")
            self.mapping = mmap.mmap(self.handle.fileno(), 0, access=mmap.ACCESS_READ)
            if hashlib.sha256(self.mapping).hexdigest() != CHECKPOINT_SHA:
                raise ValueError("External initializer file hash differs")
            size = struct.unpack_from("<Q", self.mapping)[0]
            if not 2 <= size <= 4 * 1024**2 or 8 + size >= before.st_size:
                raise ValueError("External initializer header extent differs")
            header = json.loads(self.mapping[8:8+size], object_pairs_hook=object_pairs)
            records = index["initializers"]
            if not isinstance(records, list) or len(records) != 640:
                raise ValueError("External initializer census differs")
            references = set()
            for row in records:
                name, ref = row["initializer"], row["reference"]
                if row["transform"] == "transpose":
                    # The pinned graph already contains the explicit transpose.
                    # Supply original checkpoint layout to its storage alias.
                    name += "/reference_storage"
                if not isinstance(name, str) or not name or name in self.names or not isinstance(ref, str) or ref in references or row["transform"] not in {"identity", "transpose"}:
                    raise ValueError("External initializer identity/transform differs")
                record = header[ref]
                shape, offsets = record["shape"], record["data_offsets"]
                if record["dtype"] != "F32" or not isinstance(shape, list) or not shape or any(type(n) is not int or n <= 0 for n in shape):
                    raise ValueError("External initializer dtype/shape differs")
                elements = 1
                for n in shape:
                    elements *= n
                    if elements > CHECKPOINT_BYTES // 4:
                        raise ValueError("External initializer shape exceeds file")
                if not isinstance(offsets, list) or len(offsets) != 2 or any(type(n) is not int or n < 0 for n in offsets):
                    raise ValueError("External initializer offsets invalid")
                offset, length = 8 + size + offsets[0], offsets[1] - offsets[0]
                if length != elements * 4 or offset + length > before.st_size or (offset, length) != (row["offset"], row["bytes"]):
                    raise ValueError("External initializer span differs")
                view = np.ndarray(shape, dtype="<f4", buffer=self.mapping, offset=offset)
                self.views.append(view)
                self.names.append(name)
                references.add(ref)
            after = os.fstat(self.handle.fileno())
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                raise ValueError("External initializer file changed during binding")
        except BaseException as original:
            # A local view must not keep the failed mapping exported.
            view = None
            try:
                self.close()
            except BaseException as retirement:
                raise BaseExceptionGroup("Initializer binding and retirement failed", [original, retirement]) from None
            raise

    def attach(self, ort, options):
        if self.mapping is None or self.values:
            raise ValueError("External initializers are closed or already attached")
        # This API requires user-owned buffers. Keep their read-only mapping
        # alive explicitly, including any options retained by the Python API.
        self.values = [ort.OrtValue.ortvalue_from_numpy(v) for v in self.views]
        options.add_external_initializers(self.names, self.values)

    def close(self):
        self.values.clear()
        self.views.clear()
        if self.mapping is not None:
            self.mapping.close()
            self.mapping = None
        if self.handle is not None:
            self.handle.close()
            self.handle = None
