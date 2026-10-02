"""Private single-stream FP32 ONNX reference for isolating frontend differences.

Uses the already verified three-graph export with the native decoder's window,
cache and greedy rules. The owner verifies the complete model manifest before
construction. Not a deployed backend or Windows performance qualification.
"""

import re

import numpy as np

from runtime.onnx_runtime import local_runtime
from runtime.stt.nemotron_fbank import NemotronFbank


class NemoOnnxRecognizer:
    def __init__(self, snapshot, mel_filters, *, threads=4, aligned_encoder=None,
                 encoder_provider="CPUExecutionProvider", profile_prefix=None,
                 external_initializers=False):
        self.encoder = self.decoder = self.joiner = None
        self._external_initializers = None
        try:
            self._initialize(snapshot, mel_filters, threads=threads, aligned_encoder=aligned_encoder,
                             encoder_provider=encoder_provider, profile_prefix=profile_prefix,
                             external_initializers=external_initializers)
        except BaseException as original:
            try:
                self.close()
            except BaseException as retirement:
                raise BaseExceptionGroup("Recognizer initialization and retirement failed", [original, retirement]) from None
            raise

    def _initialize(
        self,
        snapshot,
        mel_filters,
        *,
        threads=4,
        aligned_encoder=None,
        encoder_provider="CPUExecutionProvider",
        profile_prefix=None,
        external_initializers=False,
    ):
        ort = local_runtime()
        if encoder_provider not in {"CPUExecutionProvider", "DmlExecutionProvider"}:
            raise ValueError("Unqualified private encoder provider")
        if encoder_provider not in ort.get_available_providers():
            raise ValueError("Requested encoder provider is unavailable")
        self.cache_aligned = aligned_encoder is not None
        self.closed = False
        sessions = []
        if external_initializers:
            if aligned_encoder is not None and aligned_encoder != snapshot / "encoder.onnx":
                raise ValueError("External initializer binding requires the original pinned encoder")
            from runtime.stt.external_weights import EncoderInitializers

            self._external_initializers = EncoderInitializers(snapshot)
        for part in ("encoder", "decoder", "joiner"):
            options = ort.SessionOptions()
            options.intra_op_num_threads = threads
            options.inter_op_num_threads = 1
            provider = encoder_provider if part == "encoder" else "CPUExecutionProvider"
            if provider == "DmlExecutionProvider":
                options.enable_mem_pattern = False
                options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            if part == "encoder" and profile_prefix is not None:
                options.enable_profiling = True
                options.profile_file_prefix = str(profile_prefix)
            if part == "encoder" and self._external_initializers is not None:
                self._external_initializers.attach(ort, options)
            session = ort.InferenceSession(
                str(
                    aligned_encoder
                    if part == "encoder" and self.cache_aligned
                    else snapshot / (part + ".onnx")
                ),
                options,
                providers=[provider],
            )
            session.disable_fallback()
            if session.get_providers()[0] != provider:
                raise ValueError("Requested provider was replaced during construction")
            sessions.append(session)
            setattr(self, part, session)
        self.encoder, self.decoder, self.joiner = sessions
        self.metadata = self.encoder.get_modelmeta().custom_metadata_map
        required = {
            "window_size": "65",
            "chunk_shift": "56",
            "feat_dim": "128",
            "pred_rnn_layers": "2",
            "pred_hidden": "640",
            "cache_last_channel_dim1": "24",
            "cache_last_channel_dim2": "56",
            "cache_last_channel_dim3": "1024",
            "cache_last_time_dim3": "8",
        }
        if any(self.metadata.get(k) != v for k, v in required.items()):
            raise ValueError("Pinned model geometry changed")
        if self.cache_aligned and (
            self.encoder.get_inputs()[-1].name != "diagnostic_drop"
            or len(self.encoder.get_outputs()) != 5
        ):
            raise ValueError("Cache-aligned encoder contract differs")
        self.mel = mel_filters
        self.symbols = {}
        for line in (snapshot / "tokens.txt").read_text(encoding="utf-8").splitlines():
            symbol, index = line.rsplit(" ", 1)
            index = int(index)
            if index in self.symbols:
                raise ValueError("Duplicate vocabulary ID")
            self.symbols[index] = symbol
        if set(self.symbols) != set(range(13088)) or self.symbols[13087] != "<blk>":
            raise ValueError("Vocabulary binding differs")

    def create_stream(self, *, right_context=6):
        if self.closed:
            raise RuntimeError("Recognizer is closed")
        if right_context != 6 and not self.cache_aligned:
            raise ValueError("Preview requires the cache-aligned graph")
        return OnnxStream(self.mel, right_context=right_context)

    def is_ready(self, stream):
        if self.closed:
            raise RuntimeError("Recognizer is closed")
        if self.cache_aligned:
            start, count, _drop = self.window(stream)
            return start + count <= stream.features.frames_ready
        return stream.processed + 65 < stream.features.frames_ready

    def window(self, stream):
        if not self.cache_aligned:
            return stream.processed, 65, 2
        # First call has no previous acoustic context to discard. Later calls
        # retain nine mel frames; their first two encoder frames are context.
        stride = 8 * (stream.right_context + 1)
        if stream.processed == 0:
            return 0, stride - 7, 0
        return stream.processed - 16, stride + 9, 2

    def close(self):
        """Owner calls after retiring all stream work, before interpreter exit."""
        self.closed = True
        self.encoder = self.decoder = self.joiner = None
        if getattr(self, "_external_initializers", None) is not None:
            self._external_initializers.close()
            self._external_initializers = None

    def _decoder(self, stream, token):
        inputs = [
            np.array([[token]], dtype=np.int32),
            np.ones(1, dtype=np.int32),
            *stream.decoder_states,
        ]
        values = self.decoder.run(
            None,
            dict(zip([x.name for x in self.decoder.get_inputs()], inputs, strict=True)),
        )
        stream.decoder_out, length, *stream.decoder_states = values
        if length.tolist() != [1] or not np.isfinite(stream.decoder_out).all():
            raise ValueError("Invalid decoder output")

    def decode_stream(self, stream):
        if not self.is_ready(stream):
            raise ValueError("Decode requested before acoustic window is ready")
        start, count, drop = self.window(stream)
        features = stream.features.get_frames(start, count)
        inputs = [
            features.T[None].copy(),
            np.array([count], dtype=np.int64),
            *stream.encoder_states,
            np.array([0], dtype=np.int64),
        ]
        if self.cache_aligned:
            inputs.append(np.array([drop], dtype=np.int64))
        values = self.encoder.run(
            None,
            dict(zip([x.name for x in self.encoder.get_inputs()], inputs, strict=True)),
        )
        encoder, lengths, *stream.encoder_states = values
        frames = stream.right_context + 1
        if (
            encoder.shape != (1, 1024, frames)
            or lengths.tolist() != [frames]
            or not np.isfinite(encoder).all()
        ):
            raise ValueError("Pinned encoder extent or finite output differs")
        stream.processed += 8 * frames
        if stream.decoder_out is None:
            self._decoder(stream, 13087)
        for frame in range(encoder.shape[2]):
            for _ in range(10):
                logits = self.joiner.run(
                    None,
                    {
                        "encoder_outputs": encoder[:, :, frame : frame + 1].copy(),
                        "decoder_outputs": stream.decoder_out,
                    },
                )[0]
                if logits.size != 13088 or not np.isfinite(logits).all():
                    raise ValueError("Invalid joiner scores")
                token = int(np.argmax(logits))
                if token == 13087:
                    break
                if len(stream.tokens) >= 4096:
                    raise RuntimeError("Decoded token bound exceeded")
                stream.tokens.append(token)
                self._decoder(stream, token)

    def get_result(self, stream):
        symbols = [self.symbols[token] for token in stream.tokens]
        return (
            "".join(
                s for s in symbols if not re.fullmatch(r"<[a-z]{2,3}(?:-[A-Z]{2})?>", s)
            )
            .replace("▁", " ")
            .strip()
        )


class OnnxStream:
    def __init__(self, mel, *, right_context=6):
        # Zero-context was tested but failed numerical parity: do not admit it
        # merely because the graph accepts its tensor shapes. Default unchanged.
        if type(right_context) is not int or right_context not in (3, 6):
            raise ValueError("Unsupported native right context")
        self.right_context = right_context
        self.features = NemotronFbank(mel)
        self.processed = 0
        self.tokens = []
        self.decoder_out = None
        self.encoder_states = [
            np.zeros((1, 24, 56, 1024), dtype=np.float32),
            np.zeros((1, 24, 1024, 8), dtype=np.float32),
            np.zeros(1, dtype=np.int64),
        ]
        self.decoder_states = [
            np.zeros((2, 1, 640), dtype=np.float32) for _ in range(2)
        ]

    def set_option(self, key, value):
        if (key, value) != ("language", "en-US"):
            raise ValueError("Diagnostic supports explicit en-US only")

    def accept_waveform(self, rate, samples):
        self.features.accept_waveform(rate, samples)

    def input_finished(self):
        self.features.input_finished()
