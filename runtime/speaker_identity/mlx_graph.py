"""Exact-model MLX lowering candidate for full-utterance WeSpeaker inference.

Not a general ONNX runtime or a production default. Only the pinned graph's
small operator set is accepted. Shape arithmetic stays host-side; neural
floating-point operations run on an explicit MLX GPU stream. No weights,
frontend, pooling formula, input length or policy is learned/changed here.
"""

import json
from collections import Counter
from pathlib import Path

import numpy as np

from .backend import MODEL_SHA256, file_sha256

ATTRIBUTES = {
    "Add": set(),
    "Sub": set(),
    "Mul": set(),
    "Div": set(),
    "Pow": set(),
    "Relu": set(),
    "Sigmoid": set(),
    "Sqrt": set(),
    "Identity": set(),
    "Constant": {"value"},
    "Transpose": {"perm"},
    "Unsqueeze": set(),
    "Conv": {"dilations", "group", "kernel_shape", "pads", "strides"},
    "Shape": set(),
    "Gather": {"axis"},
    "Cast": {"to"},
    "ReduceMean": {"axes", "keepdims"},
    "ReduceSum": {"keepdims"},
    "Concat": {"axis"},
    "Reshape": {"allowzero"},
    "BatchNormalization": {"epsilon", "momentum", "training_mode"},
    "Softmax": {"axis"},
    "Clip": set(),
    "Gemm": {"alpha", "beta", "transB"},
}


def validate_graph(graph):
    if (
        graph.get("source_sha256") != MODEL_SHA256
        or graph.get("inputs") != ["feats"]
        or graph.get("outputs") != ["embs"]
    ):
        raise ValueError("unsupported graph identity/interface")
    available = set(graph["initializers"]) | {"feats"}
    for node in graph["nodes"]:
        op, attributes = node["op"], node["attributes"]
        if op not in ATTRIBUTES or set(attributes) - ATTRIBUTES[op]:
            raise ValueError("unsupported operator/attributes: " + op)
        if (
            any(i and i not in available for i in node["inputs"])
            or node["output"] in available
        ):
            raise ValueError("unordered, missing or duplicated graph value")
        if op == "Cast" and attributes.get("to") != 1:
            raise ValueError("only float32 shape casts are supported")
        if op == "BatchNormalization" and attributes.get("training_mode", 0) != 0:
            raise ValueError("training batch normalization refused")
        if op == "Reshape" and attributes.get("allowzero", 0) != 0:
            raise ValueError("unsupported reshape zero semantics")
        available.add(node["output"])
    if "embs" not in available:
        raise ValueError("missing model output")


def evaluate_graph(graph, initializers, features, operation):
    """Keep branch inputs until their last use, not every activation until EOF.

    MLX owns the lazy dependencies required for the final result. Keeping an
    additional Python reference to every intermediate prevents its scheduler
    from reclaiming those buffers during evaluation.
    """
    remaining = Counter(i for n in graph["nodes"] for i in n["inputs"] if i)
    values = {**initializers, "feats": features}
    for node in graph["nodes"]:
        values[node["output"]] = operation(node, values)
        for name in node["inputs"]:
            if name:
                remaining[name] -= 1
                if remaining[name] == 0:
                    del values[name]
        if not remaining[node["output"]] and node["output"] != "embs":
            del values[node["output"]]
    return values["embs"]


def load_tensors(directory, manifest):
    """Packaged weights use data-only safetensors; frozen lab NPZ stays explicit."""
    encoding = manifest.get("tensors_format", "npz")
    if encoding not in {"npz", "safetensors"}:
        raise ValueError("unsupported lowered tensor format")
    name = "tensors." + encoding
    if file_sha256(directory / name) != manifest["tensors_sha256"]:
        raise ValueError("lowered-model artifact differs")
    if encoding == "safetensors":
        from safetensors.numpy import load_file

        tensors = load_file(directory / name)
    else:
        with np.load(directory / name, allow_pickle=False) as arrays:
            tensors = {key: arrays[key] for key in arrays.files}
    if len(tensors) != manifest["tensors"]:
        raise ValueError("lowered tensor count differs")
    return tensors


class MLXSpeakerGraph:
    def __init__(self, directory: Path, expected_manifest: str):
        import mlx.core as mx

        if not mx.metal.is_available():
            raise ValueError("Apple GPU unavailable")
        if file_sha256(directory / "manifest.json") != expected_manifest:
            raise ValueError("lowered-model manifest differs")
        manifest = json.loads((directory / "manifest.json").read_text())
        if manifest["source_sha256"] != MODEL_SHA256:
            raise ValueError("lowered-model source differs")
        if file_sha256(directory / "graph.json") != manifest["graph_sha256"]:
            raise ValueError("lowered-model artifact differs")
        self.graph = json.loads((directory / "graph.json").read_text())
        validate_graph(self.graph)
        self.mx, self.stream = mx, mx.new_stream(mx.gpu)
        self.tensors = load_tensors(directory, manifest)
        with mx.stream(self.stream):
            self.initializers = {
                name: mx.array(self.tensors[key])
                if self.tensors[key].dtype.kind == "f"
                else self.tensors[key]
                for name, key in self.graph["initializers"].items()
            }
            mx.eval([a for a in self.initializers.values() if isinstance(a, mx.array)])
        # Only shape constants remain as NumPy values; these are not a second
        # inference backend. Unknown attribute/operator forms refuse execution.

    def operation(self, node, values):
        mx = self.mx
        op, attr = node["op"], node["attributes"]
        args = [values[i] if i else None for i in node["inputs"]]
        if op == "Constant":
            return self.tensors[attr["value"]["tensor"]]
        if op == "Identity":
            return args[0]
        if op == "Shape":
            return np.asarray(args[0].shape, dtype=np.int64)
        if op == "Gather":
            if isinstance(args[0], mx.array) or isinstance(args[1], mx.array):
                raise ValueError(
                    "unexpected neural Gather: pinned graph uses host shapes"
                )
            return np.take(args[0], args[1], axis=attr.get("axis", 0))
        if op == "Cast":
            return args[0].astype(
                mx.float32 if isinstance(args[0], mx.array) else np.float32
            )
        if op == "Reshape":
            if isinstance(args[1], mx.array):
                raise ValueError("reshape must be derived from host shape metadata")
            shape = [
                int(n) if n != 0 else args[0].shape[i] for i, n in enumerate(args[1])
            ]
            return args[0].reshape(shape)
        if op == "Unsqueeze":
            data, axes = args
            if isinstance(axes, mx.array):
                raise ValueError("unsqueeze axes must be metadata")
            dims = data.ndim + len(axes)
            for axis in sorted(int(a) % dims for a in axes):
                data = (
                    mx.expand_dims(data, axis)
                    if isinstance(data, mx.array)
                    else np.expand_dims(data, axis)
                )
            return data
        if op == "Concat":
            xp = mx if any(isinstance(a, mx.array) for a in args) else np
            return xp.concatenate(args, axis=attr["axis"])
        if op in {"Add", "Sub", "Mul", "Div", "Pow"}:
            xp = mx if any(isinstance(a, mx.array) for a in args) else np
            if xp is mx:
                args = [a if isinstance(a, mx.array) else mx.array(a) for a in args]
            return getattr(
                xp,
                {
                    "Add": "add",
                    "Sub": "subtract",
                    "Mul": "multiply",
                    "Div": "divide",
                    "Pow": "power",
                }[op],
            )(*args)
        if op == "Transpose":
            return args[0].transpose(attr["perm"])
        if op == "Conv":
            x, weight, bias = args
            rank = x.ndim - 2
            pads = attr.get("pads", [0] * (rank * 2))
            if rank not in (1, 2) or pads[:rank] != pads[rank:]:
                raise ValueError("unsupported convolution rank/padding")
            if tuple(weight.shape[2:]) != tuple(attr["kernel_shape"]):
                raise ValueError("convolution kernel shape differs")
            if rank == 2:
                out = mx.conv2d(
                    x.transpose(0, 2, 3, 1),
                    weight.transpose(0, 2, 3, 1),
                    stride=tuple(attr["strides"]),
                    padding=tuple(pads[:2]),
                    dilation=tuple(attr["dilations"]),
                    groups=attr["group"],
                    stream=self.stream,
                ).transpose(0, 3, 1, 2)
            else:
                out = mx.conv1d(
                    x.transpose(0, 2, 1),
                    weight.transpose(0, 2, 1),
                    stride=attr["strides"][0],
                    padding=pads[0],
                    dilation=attr["dilations"][0],
                    groups=attr["group"],
                    stream=self.stream,
                ).transpose(0, 2, 1)
            return out + bias.reshape((1, -1) + (1,) * rank)
        if op in {"ReduceMean", "ReduceSum"}:
            axes = attr["axes"] if op == "ReduceMean" else [int(a) for a in args[1]]
            fn = mx.mean if op == "ReduceMean" else mx.sum
            return fn(args[0], axis=tuple(axes), keepdims=bool(attr.get("keepdims", 1)))
        if op == "BatchNormalization":
            x, weight, bias, mean, variance = args
            shape = (1, -1) + (1,) * (x.ndim - 2)
            return (x - mean.reshape(shape)) / mx.sqrt(
                variance.reshape(shape) + attr["epsilon"]
            ) * weight.reshape(shape) + bias.reshape(shape)
        if op == "Relu":
            return mx.maximum(args[0], 0)
        if op == "Sigmoid":
            return mx.sigmoid(args[0])
        if op == "Softmax":
            return mx.softmax(args[0], axis=attr["axis"])
        if op == "Sqrt":
            return mx.sqrt(args[0])
        if op == "Clip":
            lower = (
                float(args[1])
                if len(args) > 1 and args[1] is not None
                else -float("inf")
            )
            upper = (
                float(args[2])
                if len(args) > 2 and args[2] is not None
                else float("inf")
            )
            return mx.clip(args[0], lower, upper)
        if op == "Gemm":
            x, weight, bias = args
            if attr.get("transB", 0):
                weight = weight.T
            return (
                attr.get("alpha", 1) * mx.matmul(x, weight, stream=self.stream)
                + attr.get("beta", 1) * bias
            )
        raise ValueError("unsupported operation: " + op)

    def __call__(self, features):
        mx = self.mx
        if (
            features.dtype != np.float32
            or features.ndim != 3
            or features.shape[0] != 1
            or features.shape[2] != 80
            or not 198 <= features.shape[1] <= 3000
            or not np.isfinite(features).all()
        ):
            raise ValueError("bounded full-utterance float32 features required")
        with mx.stream(self.stream):
            result = evaluate_graph(
                self.graph, self.initializers, mx.array(features), self.operation
            )
            mx.eval(result)
            output = np.asarray(result)
        if output.shape != (1, 256) or not np.isfinite(output).all():
            raise ValueError("invalid speaker embedding")
        return output
