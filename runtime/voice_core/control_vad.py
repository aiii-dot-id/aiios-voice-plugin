"""Single-thread CPU control inference, independent of the STT/TTS executor.

Silero's pinned ONNX contract: 512 samples at 16 kHz, 64 context samples,
recurrent state (2, 1, 128). No Torch dependency or implicit resampling.
State/context contract follows snakers4/silero-vad utils_vad.py (MIT).
Checkpoint is the ONNX source named by our current MLX conversion, not the
newer upstream model whose decisions failed the recorded parity comparison.
"""

import hashlib

import numpy as np

from runtime.onnx_runtime import local_runtime

REVISION = "e71cae966052b992a7eca6b17738916ce0eca4ec"
SHA256 = "a4a068cd6cf1ea8355b84327595838ca748ec29a25bc91fc82e6c299ccdc5808"


class ControlVAD:
    def __init__(self, root, *, assets=None):
        path = (
            root / "artifacts/native-vad" / REVISION / "silero_vad.onnx"
            if assets is None
            else assets.snapshot("control-vad") / "silero_vad.onnx"
        )
        data = path.read_bytes()
        if len(data) != 2243022 or hashlib.sha256(data).hexdigest() != SHA256:
            raise ValueError("native VAD checkpoint identity mismatch")
        ort = local_runtime()
        options = ort.SessionOptions()
        options.inter_op_num_threads = options.intra_op_num_threads = 1
        self.model = ort.InferenceSession(
            data, sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.identity = {
            "repository": "onnx-community/silero-vad",
            "revision": REVISION,
            "sha256": hashlib.sha256(data).hexdigest(),
            "runtime": "onnxruntime",
            "version": ort.__version__,
            "provider": self.model.get_providers(),
            "intra_threads": 1,
            "inter_threads": 1,
        }
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, 64), dtype=np.float32)

    def feed(self, samples):
        samples = np.asarray(samples, dtype=np.float32)
        if samples.shape != (512,) or not np.isfinite(samples).all():
            raise ValueError("control VAD needs 512 finite samples")
        signal = np.concatenate((self.context, samples.reshape(1, -1)), axis=1)
        probability, state = self.model.run(
            None,
            {
                "input": signal,
                "state": self.state,
                "sr": np.array(16000, dtype=np.int64),
            },
        )
        result = float(probability[0, 0])
        if not 0 <= result <= 1 or not np.isfinite(state).all():
            raise ValueError("invalid control VAD output")
        self.state = state
        self.context = signal[:, -64:].copy()
        return result
