"""Isolated batch-one Qwen talker decode candidate; unchanged model operations.

Prefill remains upstream. Decode replays the original layers against a bounded
KV workspace and explicit causal mask. Not enabled in the application until
real-cache numerical, complete-speech and interruption gates pass.
"""

import threading


class DecodeBackboneGraph:
    def __init__(self, model, torch, capacity=1024):
        if type(capacity) is not int or not 2 <= capacity <= 2048:
            raise ValueError("Bounded decode capacity required")
        config = model.config
        if (
            model.training
            or config.sliding_window is not None
            or config._attn_implementation != "sdpa"
            or model.rotary_emb.rope_type != "default"
        ):
            raise ValueError(
                "Only evaluated full-attention default-RoPE SDPA is qualified"
            )
        self.model, self.torch, self.capacity = model, torch, capacity
        self.original = model.forward
        self.graph = self.current_cache = None
        self.length = self.replays = 0
        self.lock = threading.Lock()

    def update(self, keys, values, layer_idx, cache_kwargs):
        position = cache_kwargs["cache_position"]
        self.keys[layer_idx].index_copy_(2, position, keys)
        self.values[layer_idx].index_copy_(2, position, values)
        return self.keys[layer_idx], self.values[layer_idx]

    def eager(self):
        torch, model = self.torch, self.model
        mask = (
            torch.where(
                self.slots <= self.position[0], 0.0, torch.finfo(self.inputs.dtype).min
            )
            .to(self.inputs.dtype)
            .view(1, 1, 1, -1)
        )
        hidden = self.inputs
        positions = model.rotary_emb(hidden, self.position_ids)
        states = [hidden]
        for layer in model.layers:
            hidden = layer(
                hidden,
                attention_mask=mask,
                position_ids=self.position_ids[0],
                past_key_values=self,
                output_attentions=False,
                use_cache=True,
                cache_position=self.position,
                position_embeddings=positions,
            )[0]
            states.append(hidden)
        states[-1] = model.norm(hidden)
        # One contiguous owned result avoids one GPU clone launch per layer.
        return torch.stack(states)

    def prepare(self):
        if self.graph is not None:
            raise RuntimeError("Decode graph already prepared")
        torch, config = self.torch, self.model.config
        parameter = next(self.model.parameters())
        if parameter.device.type != "cuda" or parameter.dtype != torch.float16:
            raise ValueError("Candidate requires CUDA FP16 backbone")
        self.stream = torch.cuda.current_stream()
        self.inputs = torch.zeros(
            (1, 1, config.hidden_size), dtype=parameter.dtype, device=parameter.device
        )
        self.position = torch.zeros(1, dtype=torch.long, device=parameter.device)
        self.position_ids = torch.zeros(
            (3, 1, 1), dtype=torch.long, device=parameter.device
        )
        self.slots = torch.arange(self.capacity, device=parameter.device)
        shape = (
            len(self.model.layers),
            1,
            config.num_key_value_heads,
            self.capacity,
            getattr(
                config, "head_dim", config.hidden_size // config.num_attention_heads
            ),
        )
        self.keys = torch.zeros(shape, dtype=parameter.dtype, device=parameter.device)
        self.values = torch.zeros_like(self.keys)
        side = torch.cuda.Stream()
        side.wait_stream(self.stream)
        with torch.cuda.stream(side), torch.inference_mode():
            for _ in range(3):
                self.eager()
        self.stream.wait_stream(side)
        torch.cuda.synchronize()
        graph = torch.cuda.CUDAGraph()
        with torch.inference_mode(), torch.cuda.graph(graph):
            self.outputs = self.eager()
        self.graph = graph
        self.keys.zero_()
        self.values.zero_()

    def __call__(self, *args, **kwargs):
        from transformers.modeling_outputs import BaseModelOutputWithPast

        if args or kwargs.get("inputs_embeds") is None:
            raise ValueError("Explicit keyword embedding input required")
        inputs = kwargs["inputs_embeds"]
        if inputs.shape[1] > 1:
            if not self.lock.acquire(blocking=False):
                raise RuntimeError("Concurrent prefill refused")
            try:
                self.current_cache = None
                return self.original(**kwargs)
            finally:
                self.lock.release()
        if self.graph is None:
            raise RuntimeError("Decode graph must be prepared before admission")
        allowed = {
            "input_ids",
            "attention_mask",
            "position_ids",
            "past_key_values",
            "inputs_embeds",
            "use_cache",
            "output_attentions",
            "output_hidden_states",
            "cache_position",
            "return_dict",
        }
        if (
            set(kwargs) - allowed
            or kwargs.get("input_ids") is not None
            or kwargs.get("output_attentions")
            or not kwargs.get("use_cache")
            or kwargs.get("return_dict") is False
            or tuple(inputs.shape) != tuple(self.inputs.shape)
            or inputs.dtype != self.inputs.dtype
            or inputs.device != self.inputs.device
            or self.torch.cuda.current_stream() != self.stream
        ):
            raise ValueError("Decode call differs from pinned graph contract")
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("Concurrent decode refused")
        try:
            with self.torch.inference_mode():
                cache = kwargs["past_key_values"]
                position = kwargs["cache_position"]
                if position is None or tuple(position.shape) != (1,):
                    raise ValueError("One explicit cache position required")
                index = int(position.item())
                mask = kwargs.get("attention_mask")
                pos = kwargs.get("position_ids")
                if (
                    not 0 < index < self.capacity
                    or mask is None
                    or tuple(mask.shape) != (1, index + 1)
                    or not bool((mask == 1).all())
                    or pos is None
                    or tuple(pos.shape) != (3, 1, 1)
                ):
                    raise ValueError("Unpadded bounded batch-one decode required")
                if cache is not self.current_cache:
                    if not hasattr(cache, "layers") or len(cache.layers) != len(
                        self.model.layers
                    ):
                        raise ValueError("Exact populated layer cache required")
                    self.keys.zero_()
                    self.values.zero_()
                    for i, layer in enumerate(cache.layers):
                        if tuple(layer.keys.shape) != tuple(
                            self.keys[i, :, :, :index].shape
                        ):
                            raise ValueError("Prefill cache dimensions differ")
                        self.keys[i, :, :, :index].copy_(layer.keys)
                        self.values[i, :, :, :index].copy_(layer.values)
                    self.current_cache, self.length = cache, index
                if index != self.length:
                    raise ValueError("Decode cache position is not consecutive")
                self.inputs.copy_(inputs)
                self.position.copy_(position)
                self.position_ids.copy_(pos)
                self.graph.replay()
                self.length += 1
                self.replays += 1
                # Keep the caller's actual Cache type and logical length. Views
                # expose only the committed prefix, never the unused capacity.
                for i, layer in enumerate(cache.layers):
                    layer.keys = self.keys[i, :, :, : self.length]
                    layer.values = self.values[i, :, :, : self.length]
                states = self.outputs.clone().unbind(0)
                return BaseModelOutputWithPast(
                    last_hidden_state=states[-1],
                    past_key_values=cache,
                    hidden_states=states
                    if kwargs.get("output_hidden_states")
                    else None,
                    attentions=None,
                )
        finally:
            self.lock.release()
