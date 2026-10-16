"""Export the pinned two-speaker waveform separator with numerical parity.

Development tool only. A graph export is not speaker attribution, transcript
separation, installed qualification or release approval. Outputs remain raw
floating-point estimates: a caller must restore their scale before PCM16.
"""
import argparse
import copy
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

UPSTREAM_REVISION = '6b3774dc79c46ae8bed2a4fa5f706f0ac8c75c61'
CHECKPOINT_SHA256 = '00a3a48bda492db1e829b85dd443f8f43a43039a3e90f1a24962ea9caf14a11a'


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def write(path, value):
    with path.open('x') as f:
        json.dump(value, f, indent=2, allow_nan=False)


def accurate_group_norms(owner):
    """Avoid a multi-million-element float32 InstanceNormalization reduction.

    The exporter lowers GroupNorm to InstanceNormalization. Its CPU kernel
    loses enough precision on long flattened sequences to change the output.
    Preserve the formula and weights, but accumulate centered statistics in
    double precision. The untouched upstream owner remains the reference.
    """
    import torch

    class GroupNorm(torch.nn.Module):
        def __init__(self, norm):
            super().__init__()
            self.groups, self.eps = norm.num_groups, norm.eps
            self.weight, self.bias = norm.weight, norm.bias

        def forward(self, x):
            grouped = x.to(torch.float64).reshape(x.shape[0], self.groups, -1)
            centered = grouped-grouped.mean(dim=-1, keepdim=True)
            variance = (centered*centered).mean(dim=-1, keepdim=True)
            normalized = (centered/torch.sqrt(variance+self.eps)).reshape_as(x).to(x.dtype)
            if self.weight is None:
                return normalized
            shape = [1, -1]+[1]*(x.dim()-2)
            return normalized*self.weight.reshape(shape)+self.bias.reshape(shape)

    result = copy.deepcopy(owner)
    count = 0

    def replace(module):
        nonlocal count
        for name, child in list(module.named_children()):
            if isinstance(child, torch.nn.GroupNorm):
                setattr(module, name, GroupNorm(child))
                count += 1
            else:
                replace(child)
    replace(result)
    return result, count


def rank_stable_fsmns(copied_owner, fsmn_type):
    """Remove only the channel axis inserted for the FSMN's 2-D convolution.

    Upstream's axis-free squeeze also removes a singleton batch/time axis and
    relies on the residual add to broadcast it back. With dynamic time that
    makes ONNX's intermediate rank unknowable to Core ML. Keep [batch,time,
    features] throughout, with exactly the same learned operations/weights.
    Apply only to the export copy; the upstream reference is never rewritten.
    """
    import torch

    class FSMN(torch.nn.Module):
        def __init__(self, source):
            super().__init__()
            self.linear, self.project, self.conv = source.linear, source.project, source.conv

        def forward(self, x):
            projected = self.project(torch.nn.functional.relu(self.linear(x)))
            convolution = self.conv(projected.unsqueeze(1).permute(0, 3, 2, 1))
            # Only this inserted channel is removed. Keep batch/time intact;
            # axis-free squeeze makes the residual depend on broadcasting.
            return x+convolution.permute(0, 3, 2, 1).squeeze(1)

    count = 0
    def replace(module):
        nonlocal count
        for name, child in list(module.named_children()):
            if isinstance(child, fsmn_type):
                setattr(module, name, FSMN(child))
                count += 1
            else:
                replace(child)
    replace(copied_owner)
    return count


def explicit_last_index_gathers(model):
    """Normalize Gather's constant -1 indices using the actual axis length.

    Core ML's conversion of a negative constant index on a symbolic extent
    can select element zero instead of the last element. Shape(data)[axis]-1
    expresses the same index without asking a converter to guess the extent.
    Only all-minus-one indices are rewritten: a more-negative invalid index
    must not become an accidentally valid negative index after addition.
    Empty axes still fail as before. No tensor values/weights are changed.
    """
    import onnx
    import numpy as np
    constants = {t.name: t for t in model.graph.initializer}
    for node in model.graph.node:
        if node.op_type == 'Constant':
            for attr in node.attribute:
                if attr.name == 'value': constants[node.output[0]] = attr.t
    occupied = {name for node in model.graph.node for name in (*node.input, *node.output)}
    occupied.update(t.name for t in model.graph.initializer)
    occupied.update(v.name for v in (*model.graph.input, *model.graph.output, *model.graph.value_info))
    nodes, count = [], 0
    for node in model.graph.node:
        index = constants.get(node.input[1]) if node.op_type == 'Gather' else None
        if index is not None and index.data_type == onnx.TensorProto.INT64:
            values = onnx.numpy_helper.to_array(index)
            axis = next((a.i for a in node.attribute if a.name == 'axis'), 0)
            # Shape-derived gathers use axis 0; leave other axes untouched.
            if values.size and np.all(values == -1) and axis == 0:
                prefix = f'aii_last_index_{count}_'
                names = {role: prefix+role for role in ('shape', 'length', 'axis', 'index')}
                if occupied.intersection(names.values()):
                    raise ValueError('generated gather name collides')
                occupied.update(names.values())
                model.graph.initializer.append(onnx.numpy_helper.from_array(np.array(0, dtype=np.int64), names['axis']))
                nodes.append(onnx.helper.make_node('Shape', [node.input[0]], [names['shape']]))
                nodes.append(onnx.helper.make_node('Gather', [names['shape'], names['axis']], [names['length']], axis=0))
                nodes.append(onnx.helper.make_node('Add', [names['length'], node.input[1]], [names['index']]))
                node.input[1] = names['index']
                count += 1
        nodes.append(node)
    del model.graph.node[:]
    model.graph.node.extend(nodes)
    return count


def explicit_attention_contractions(model):
    """Lower the pinned attention equations without expanded Einsum scratch.

    Learned tensors, contraction axes and complete dynamic time extents are
    unchanged. Ordinary MatMul handles the grouped shared matrix by broadcast;
    only the global reduction flattens its two contracted sequence axes. Do
    not approximate attention, prune time, or specialize a trace's length.
    Unknown equations/ranks refuse rather than silently changing a new model.
    """
    import onnx
    import numpy as np

    inferred = onnx.shape_inference.infer_shapes(model)
    ranks = {v.name: len(v.type.tensor_type.shape.dim) for v in
             (*inferred.graph.input, *inferred.graph.value_info, *inferred.graph.output)
             if v.type.tensor_type.HasField('shape')}
    ranks.update({t.name: len(t.dims) for t in model.graph.initializer})
    occupied = {name for n in model.graph.node for name in (*n.input, *n.output)}
    occupied.update(t.name for t in model.graph.initializer)
    nodes, constants, count = [], [], 0
    for node in model.graph.node:
        if node.op_type != 'Einsum':
            nodes.append(node)
            continue
        equations = [a for a in node.attribute if a.name == 'equation']
        if len(equations) != 1 or len(node.input) != 2 or len(node.output) != 1:
            raise ValueError('separator contraction signature differs')
        equation = onnx.helper.get_attribute_value(equations[0]).decode().replace(' ', '')
        left, right = node.input
        output = node.output[0]
        prefix = f'aii_attention_{count}_'
        def name(role):
            value = prefix+role
            if value in occupied:
                raise ValueError('generated attention name collides')
            occupied.add(value)
            return value
        def constant(role, values):
            value = name(role)
            constants.append(onnx.numpy_helper.from_array(np.array(values, dtype=np.int64), value))
            return value
        def emit(op, inputs, target=None, **attrs):
            value = target if target is not None else name(f'intermediate_{len(nodes)}')
            nodes.append(onnx.helper.make_node(op, inputs, [value], name=name(f'node_{len(nodes)}'), **attrs))
            return value
        def unsqueeze(value, axis):
            return emit('Unsqueeze', [value, constant(f'axes_{len(nodes)}', [axis])])
        def transpose_tail(value):
            rank = ranks.get(value)
            if rank is None or rank < 2:
                raise ValueError('separator attention rank unavailable')
            permutation = list(range(rank))
            permutation[-2:] = permutation[-2:][::-1]
            return emit('Transpose', [value], perm=permutation)
        def flatten_sequence(value):
            if ranks.get(value) != 4:
                raise ValueError('separator global attention rank differs')
            shape = emit('Shape', [value])
            batch = emit('Gather', [shape, constant(f'batch_{len(nodes)}', [0])], axis=0)
            width = emit('Gather', [shape, constant(f'width_{len(nodes)}', [3])], axis=0)
            target = emit('Concat', [batch, constant(f'extent_{len(nodes)}', [-1]), width], axis=0)
            return emit('Reshape', [value, target])

        if equation == '...,f->...f':
            emit('Mul', [unsqueeze(left, -1), right], output)
        elif equation == '...d,hd->...hd':
            if ranks.get(right) != 2:
                raise ValueError('separator attention head rank differs')
            emit('Mul', [unsqueeze(left, -2), right], output)
        elif equation == 'i,j->ij':
            if ranks.get(left) != 1 or ranks.get(right) != 1:
                raise ValueError('separator position rank differs')
            emit('Mul', [unsqueeze(left, 1), right], output)
        elif equation == '...id,...jd->...ij':
            emit('MatMul', [left, transpose_tail(right)], output)
        elif equation == '...ij,...jd->...id':
            emit('MatMul', [left, right], output)
        elif equation == 'bgnd,bgne->bde':
            first, second = flatten_sequence(left), flatten_sequence(right)
            transposed = emit('Transpose', [first], perm=[0, 2, 1])
            emit('MatMul', [transposed, second], output)
        elif equation == 'bgnd,bde->bgne':
            if ranks.get(left) != 4 or ranks.get(right) != 3:
                raise ValueError('separator shared attention rank differs')
            emit('MatMul', [left, unsqueeze(right, 1)], output)
        else:
            raise ValueError('separator attention equation differs')
        count += 1
    del model.graph.node[:]
    model.graph.node.extend(nodes)
    model.graph.initializer.extend(constants)
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(mode=0o700, parents=True, exist_ok=False)
    start = time.monotonic()
    result = {'passed': False, 'scope': 'native graph numerical parity only',
              'installed': False, 'speaker_identity_qualified': False}
    try:
        rev = subprocess.check_output(['git', '-C', str(args.source), 'rev-parse', 'HEAD'], text=True).strip()
        dirty = subprocess.check_output(['git', '-C', str(args.source), 'status', '--porcelain'], text=True)
        if rev != UPSTREAM_REVISION or dirty:
            raise ValueError('exact clean upstream source required')
        if digest(args.checkpoint) != CHECKPOINT_SHA256:
            raise ValueError('checkpoint differs')
        import numpy as np
        import torch
        import onnx
        import onnxruntime as ort
        sys.path.insert(0, str(args.source / 'clearvoice/clearvoice'))
        from models.mossformer2_ss.mossformer2 import MossFormer2_SS_16K
        from models.mossformer2_ss.fsmn import UniDeepFsmn_dilated
        from rotary_embedding_torch import RotaryEmbedding
        torch.set_num_threads(4)
        torch.manual_seed(28)
        owner = MossFormer2_SS_16K(SimpleNamespace(
            encoder_embedding_dim=512, mossformer_sequence_dim=512,
            num_mossformer_layer=24, encoder_kernel_size=16, num_spks=2)).eval()
        state = torch.load(args.checkpoint, weights_only=True, map_location='cpu')['model']
        owner.model.load_state_dict(state, strict=True)
        del state
        # Cache reuse is a Python implementation optimization, not graph state.
        # Do not freeze the trace length into cached rotary position tensors.
        for module in owner.modules():
            if isinstance(module, RotaryEmbedding):
                module.cache_if_possible = False

        class Export(torch.nn.Module):
            def __init__(self, model):
                super().__init__()
                self.model = model

            def forward(self, pcm):
                sources = torch.stack(self.model(pcm), dim=1)
                # The upstream Python branch pads a sub-stride decoder tail.
                # Make this shape operation explicit rather than trace a
                # branch chosen only for a divisible-by-eight example.
                return torch.nn.functional.pad(sources, (0, 8))[:, :, :pcm.shape[1]]

        converted, norms = accurate_group_norms(owner)
        if norms != 2:
            raise ValueError('pinned model GroupNorm census differs')
        fsmns = rank_stable_fsmns(converted, UniDeepFsmn_dilated)
        if fsmns != 24:
            raise ValueError('pinned model FSMN census differs')
        model = Export(converted).eval()
        reference = Export(owner).eval()
        graph = args.out / 'separator.onnx'
        # Include lengths at the attention-group boundary and convolution tail.
        # A trace whose dynamic shape is cosmetic must fail before any handoff.
        cases = [32000, 32776, 72000, 80000, 80003]
        with torch.inference_mode():
            torch.onnx.export(model, (torch.randn(1, 72000)*.1,), str(graph),
                input_names=['pcm'], output_names=['sources'], opset_version=17,
                dynamic_axes={'pcm': {1: 'samples'}, 'sources': {2: 'samples'}},
                dynamo=False, external_data=False)
        exported = onnx.load(graph)
        gathers = explicit_last_index_gathers(exported)
        contractions = explicit_attention_contractions(exported)
        if contractions != 289:
            raise ValueError('pinned model attention census differs')
        onnx.save(exported, graph)
        onnx.checker.check_model(str(graph))
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(str(graph), sess_options=options,
                                       providers=['CPUExecutionProvider'])
        if [x.name for x in session.get_inputs()] != ['pcm'] or [x.name for x in session.get_outputs()] != ['sources']:
            raise ValueError('graph interface differs')
        parity = []
        failures = []
        for samples in cases:
            pcm = torch.randn(1, samples)*.1
            with torch.inference_mode():
                expected = reference(pcm).numpy()
            # Retain byte-bound references for the independent C++ executable.
            # These are seeded diagnostic signals, never microphone audio.
            input_path = args.out / f'input-{samples}.f32'
            reference_path = args.out / f'reference-{samples}.f32'
            pcm.numpy().astype('<f4').tofile(input_path)
            expected.astype('<f4').tofile(reference_path)
            begin = time.monotonic()
            actual = session.run(['sources'], {'pcm': pcm.numpy()})[0]
            elapsed = time.monotonic()-begin
            if actual.shape != (1, 2, samples) or not np.isfinite(actual).all():
                raise ValueError('invalid separator shape or values')
            # Scale-invariant training can amplify raw amplitudes. Compare
            # complete float outputs before normalization or quantization.
            difference = np.linalg.norm(actual-expected)/max(np.linalg.norm(expected), 1e-12)
            parity.append({'samples': samples, 'relative_l2_error': float(difference),
                           'maximum_absolute_error': float(np.max(np.abs(actual-expected))),
                           'ort_seconds': elapsed, 'input_sha256': digest(input_path),
                           'reference_sha256': digest(reference_path)})
            write(args.out / f'parity-{samples}.json', parity[-1])
            if not np.isfinite(difference) or difference > 2e-4:
                failures.append(samples)
        result.update(graph_sha256=digest(graph), graph_bytes=graph.stat().st_size,
            source_revision=rev, checkpoint_sha256=CHECKPOINT_SHA256, script_sha256=digest(__file__),
            parity=parity, group_norms=norms, rank_stable_fsmns=fsmns, explicit_last_gathers=gathers,
            explicit_attention_contractions=contractions,
            reference='unmodified upstream weights and normalization',
            versions={name: importlib.metadata.version(name) for name in
                ('torch', 'onnx', 'onnxruntime', 'einops', 'rotary-embedding-torch')})
        if failures:
            result['failed_lengths'] = failures
            raise ValueError('separator numerical parity failed')
        result['passed'] = True
    except BaseException as e:
        result['failure'] = str(e)
        raise
    finally:
        result['elapsed_seconds'] = time.monotonic()-start
        write(args.out/'result.json', result)


if __name__ == '__main__':
    main()
