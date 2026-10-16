"""Candidate fixed-length CUDA execution of the pinned 15-code predictor.

Qualification compares the original generator and this path using identical
inputs and RNG states before enabling it in the Windows application. Only
the exact current sampling contract is accepted; weights remain shared/read-only.
Each invocation starts with a fresh logical KV cache. The graph owns its buffers
and callers receive a clone, never a view that a later replay can overwrite.
"""

import threading
from types import SimpleNamespace


class PredictorGraph:
    def __init__(self, predictor, torch):
        self.predictor, self.torch = predictor, torch
        self.original = predictor.generate
        self.graph = self.inputs = self.output = None
        self.replays = 0
        self._lock = threading.Lock()
        self._stream = None

    @staticmethod
    def validate_generation_config(actual, defaults):
        ignored = {"_from_model_config", "transformers_version"}
        if {k: v for k, v in actual.items() if k not in ignored} != {
            k: v for k, v in defaults.items() if k not in ignored
        }:
            raise ValueError(
                "Predictor generation defaults differ from the qualified vanilla contract"
            )

    def validate(self, kwargs):
        expected = {
            "max_new_tokens": 15,
            "do_sample": True,
            "top_p": 1.0,
            "top_k": 50,
            "temperature": 0.9,
            "output_hidden_states": True,
            "return_dict_in_generate": True,
        }
        if set(kwargs) != {*expected, "inputs_embeds"} or any(
            kwargs[k] != v for k, v in expected.items()
        ):
            raise ValueError(
                "Predictor graph requires the exact qualified sampling contract"
            )
        values = kwargs["inputs_embeds"]
        if (
            values.shape != (1, 2, 1024)
            or values.device.type != "cuda"
            or values.dtype != self.torch.float16
        ):
            raise ValueError("Predictor graph input shape/device/precision differs")

    def eager(self, values):
        from transformers.generation.logits_process import (
            TemperatureLogitsWarper,
            TopKLogitsWarper,
        )

        torch = self.torch
        temperature, top_k = TemperatureLogitsWarper(0.9), TopKLogitsWarper(50)
        cache, token = None, None
        tokens = []
        for step in range(15):
            result = self.predictor(
                input_ids=token,
                inputs_embeds=values if step == 0 else None,
                past_key_values=cache,
                use_cache=True,
                output_hidden_states=False,
                return_dict=True,
                generation_steps=step,
            )
            cache = result.past_key_values
            scores = result.logits[:, -1, :].float()
            scores = top_k(None, temperature(None, scores))
            token = torch.multinomial(torch.softmax(scores, dim=-1), 1)
            tokens.append(token)
        return torch.cat(tokens, dim=1)

    def prepare(self, values):
        from transformers import GenerationConfig

        torch = self.torch
        if self.graph is not None:
            raise RuntimeError("Predictor graph is already prepared")
        self.validate_generation_config(
            self.predictor.generation_config.to_dict(), GenerationConfig().to_dict()
        )
        state = torch.cuda.get_rng_state()
        self._stream = torch.cuda.current_stream()
        self.inputs = values.detach().clone()
        try:
            stream = torch.cuda.Stream()
            stream.wait_stream(torch.cuda.current_stream())
            with torch.cuda.stream(stream), torch.inference_mode():
                for _ in range(3):
                    self.eager(self.inputs)
            torch.cuda.current_stream().wait_stream(stream)
            torch.cuda.synchronize()
            graph = torch.cuda.CUDAGraph()
            with torch.inference_mode(), torch.cuda.graph(graph):
                self.output = self.eager(self.inputs)
            self.graph = graph
        finally:
            torch.cuda.set_rng_state(state)

    def __call__(self, **kwargs):
        self.validate(kwargs)
        if self.graph is None:
            raise RuntimeError("Predictor graph must be prepared before admission")
        if self.torch.cuda.current_stream() != self._stream:
            raise RuntimeError("Predictor graph requires its prepared CUDA stream")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("Concurrent predictor graph invocation refused")
        try:
            # Inference mode is thread-local; generation may only use no_grad.
            with self.torch.inference_mode():
                self.inputs.copy_(kwargs["inputs_embeds"])
                self.graph.replay()
                self.replays += 1
                return SimpleNamespace(sequences=self.output.clone())
        finally:
            self._lock.release()
