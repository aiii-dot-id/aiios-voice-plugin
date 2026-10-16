"""Isolated decode graphs with upstream, exact-length attention and KV cache.

Only fixed-shape projection/MLP work is captured. Attention, mask construction,
and growing Cache.update remain the upstream implementation. Not enabled in
the application; the unchanged numerical/token/audio gates still apply.
"""

import importlib
import threading


class SegmentedDecodeGraph:
    def __init__(self, model, torch, capacity=1024):
        if type(capacity) is not int or not 2 <= capacity <= 2048:
            raise ValueError("Bounded decode capacity required")
        if (
            model.training
            or model.config.sliding_window is not None
            or model.config._attn_implementation != "sdpa"
            or model.rotary_emb.rope_type != "default"
        ):
            raise ValueError(
                "Only evaluated full-attention default-RoPE SDPA is qualified"
            )
        self.model, self.torch, self.capacity = model, torch, capacity
        self.upstream = importlib.import_module(type(model).__module__)
        self.original = model.forward
        self.graphs = None
        self.lock = threading.Lock()
        self.replays = 0

    def finish_layer(self, index):
        layer = self.model.layers[index]
        hidden = self.hidden + layer.self_attn.o_proj(self.attended)
        return hidden + layer.mlp(layer.post_attention_layernorm(hidden))

    def stage(self, index):
        if index:
            self.hidden.copy_(self.finish_layer(index - 1))
        self.states[index].copy_(self.hidden)
        layer = self.model.layers[index]
        attention = layer.self_attn
        hidden = layer.input_layernorm(self.hidden)
        shape = (*hidden.shape[:-1], -1, attention.head_dim)
        query = attention.q_norm(attention.q_proj(hidden).view(shape)).transpose(1, 2)
        key = attention.k_norm(attention.k_proj(hidden).view(shape)).transpose(1, 2)
        value = attention.v_proj(hidden).view(shape).transpose(1, 2)
        query, key = self.upstream.apply_multimodal_rotary_pos_emb(
            query,
            key,
            self.cos,
            self.sin,
            attention.rope_scaling["mrope_section"],
            attention.rope_scaling["interleaved"],
        )
        return query, key, value

    def finish(self):
        self.states[-1].copy_(
            self.model.norm(self.finish_layer(len(self.model.layers) - 1))
        )

    def prepare(self):
        if self.graphs is not None:
            raise RuntimeError("Decode graphs already prepared")
        torch = self.torch
        parameter = next(self.model.parameters())
        if parameter.device.type != "cuda" or parameter.dtype != torch.float16:
            raise ValueError("Candidate requires CUDA FP16 backbone")
        self.stream = torch.cuda.current_stream()
        self.hidden = torch.zeros(
            (1, 1, self.model.config.hidden_size),
            dtype=parameter.dtype,
            device=parameter.device,
        )
        attention = self.model.layers[0].self_attn
        self.attended = torch.zeros(
            (1, 1, self.model.config.num_attention_heads * attention.head_dim),
            dtype=parameter.dtype,
            device=parameter.device,
        )
        self.states = torch.zeros(
            (len(self.model.layers) + 1, *self.hidden.shape),
            dtype=parameter.dtype,
            device=parameter.device,
        )
        self.cos, self.sin = self.model.rotary_emb(
            self.hidden,
            torch.zeros((3, 1, 1), dtype=torch.long, device=parameter.device),
        )
        graphs = []
        side = torch.cuda.Stream()
        for index in range(len(self.model.layers) + 1):
            run = (
                (lambda i=index: self.stage(i))
                if index < len(self.model.layers)
                else self.finish
            )
            side.wait_stream(self.stream)
            with torch.cuda.stream(side), torch.inference_mode():
                for _ in range(3):
                    run()
            self.stream.wait_stream(side)
            torch.cuda.synchronize()
            graph = torch.cuda.CUDAGraph()
            with torch.inference_mode(), torch.cuda.graph(graph):
                outputs = run()
            graphs.append((graph, outputs))
        self.graphs = graphs

    def __call__(self, *args, **kwargs):
        from transformers.modeling_outputs import BaseModelOutputWithPast

        if args or kwargs.get("inputs_embeds") is None:
            raise ValueError("Explicit keyword embedding input required")
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("Concurrent backbone invocation refused")
        try:
            inputs = kwargs["inputs_embeds"]
            if inputs.shape[1] > 1:
                return self.original(**kwargs)
            if self.graphs is None:
                raise RuntimeError("Decode graphs must be prepared before admission")
            torch = self.torch
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
                or inputs.shape != self.hidden.shape
                or inputs.device != self.hidden.device
                or inputs.dtype != self.hidden.dtype
                or torch.cuda.current_stream() != self.stream
            ):
                raise ValueError("Decode call differs from pinned graph contract")
            cache, position = (
                kwargs.get("past_key_values"),
                kwargs.get("cache_position"),
            )
            pos = kwargs.get("position_ids")
            if (
                position is None
                or position.shape != (1,)
                or position.dtype != torch.long
                or position.device.type not in ("cpu", "cuda")
                or pos is None
                or pos.shape != (3, 1, 1)
                or pos.dtype not in (torch.long, torch.float32)
                or pos.device.type not in ("cpu", "cuda")
                or cache is None
            ):
                raise ValueError(
                    "Exact populated batch-one cache/position required: "
                    + repr(
                        [
                            (tuple(v.shape), str(v.dtype), str(v.device))
                            if v is not None
                            else None
                            for v in (position, pos)
                        ]
                    )
                )
            index = int(position.item())
            if not 0 < index < self.capacity or cache.get_seq_length() != index:
                raise ValueError("Decode cache position differs or exceeds capacity")
            with torch.inference_mode():
                mask = self.upstream.create_causal_mask(
                    config=self.model.config,
                    input_embeds=inputs,
                    attention_mask=kwargs.get("attention_mask"),
                    cache_position=position,
                    past_key_values=cache,
                    position_ids=pos[0],
                )
                cos, sin = self.model.rotary_emb(inputs, pos)
                self.hidden.copy_(inputs)
                self.cos.copy_(cos)
                self.sin.copy_(sin)
                interface = self.upstream.ALL_ATTENTION_FUNCTIONS["sdpa"]
                for index, layer in enumerate(self.model.layers):
                    graph, (query, key, value) = self.graphs[index]
                    graph.replay()
                    key, value = cache.update(
                        key,
                        value,
                        layer.self_attn.layer_idx,
                        {"sin": sin, "cos": cos, "cache_position": position},
                    )
                    output, _ = interface(
                        layer.self_attn,
                        query,
                        key,
                        value,
                        mask,
                        dropout=0.0,
                        scaling=layer.self_attn.scaling,
                        sliding_window=None,
                        position_ids=pos[0],
                        output_attentions=False,
                        use_cache=True,
                    )
                    self.attended.copy_(output.reshape(1, 1, -1).contiguous())
                self.graphs[-1][0].replay()
                self.replays += 1
                states = self.states.clone().unbind(0)
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
