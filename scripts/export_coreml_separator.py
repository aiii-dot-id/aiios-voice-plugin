"""Reproducible development export of the pinned native Mac separator.

No plugin/runtime dependency. Independently hashed upstream waveforms, not
the export copy, are the numerical reference. This does not qualify identity,
physical GPU placement, older operating systems, or an installed worker.
"""
import argparse
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import subprocess
import sys
import time

from scripts.export_mossformer2_separator import (
    CHECKPOINT_SHA256, UPSTREAM_REVISION, digest, rank_stable_fsmns, write,
)


def coreml_copy(owner, fsmn_type, offset_type):
    import torch

    class WidthOneConv(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            if (m.kernel_size[1], m.stride[1], m.padding[1], m.dilation[1], m.padding_mode) != (1, 1, 0, 1, 'zeros'):
                raise ValueError('convolution is not width-one')
            self.weight, self.bias = m.weight, m.bias
            self.stride, self.padding, self.dilation, self.groups = m.stride[0], m.padding[0], m.dilation[0], m.groups

        def forward(self, x):
            return torch.nn.functional.conv1d(x.squeeze(-1), self.weight.squeeze(-1), self.bias,
                self.stride, self.padding, self.dilation, self.groups).unsqueeze(-1)

    class CenteredGroupNorm(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.groups, self.eps, self.weight, self.bias = m.num_groups, m.eps, m.weight, m.bias

        def forward(self, x):
            grouped = x.reshape(x.shape[0], self.groups, x.shape[1]//self.groups, x.shape[2])
            centered = grouped-grouped.mean(dim=-1, keepdim=True).mean(dim=-2, keepdim=True)
            variance = (centered*centered).mean(dim=-1, keepdim=True).mean(dim=-2, keepdim=True)
            normalized = (centered/torch.sqrt(variance+self.eps)).reshape_as(x)
            if self.weight is None:
                return normalized
            return normalized*self.weight.reshape(1, -1, 1)+self.bias.reshape(1, -1, 1)

    class ExplicitOffsetScale(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.gamma, self.beta = m.gamma, m.beta

        def forward(self, x):
            return (x.unsqueeze(-2)*self.gamma+self.beta).unbind(dim=-2)

    result = copy.deepcopy(owner)
    counts = {'convolutions': 0, 'group_norms': 0, 'offset_scales': 0}
    def replace(m):
        for name, child in list(m.named_children()):
            for kind, adapter, key in ((torch.nn.Conv2d, WidthOneConv, 'convolutions'),
                    (torch.nn.GroupNorm, CenteredGroupNorm, 'group_norms'),
                    (offset_type, ExplicitOffsetScale, 'offset_scales')):
                if isinstance(child, kind):
                    setattr(m, name, adapter(child)); counts[key] += 1
                    break
            else:
                replace(child)
    replace(result)
    counts['fsmns'] = rank_stable_fsmns(result, fsmn_type)
    return result, counts


@contextmanager
def trace_operations(rotary, blocks):
    """Scoped, single-process trace lowering; restore globals even on failure."""
    import torch
    pad, rotary_einsum, block_einsum = torch.nn.functional.pad, rotary.einsum, blocks.einsum
    def attention(equation, a, b):
        if equation == '... i d, ... j d -> ... i j': return a @ b.transpose(-1, -2)
        if equation == '... i j, ... j d -> ... i d': return a @ b
        if equation == 'b g n d, b g n e -> b d e': return a.flatten(1, 2).transpose(-1, -2) @ b.flatten(1, 2)
        if equation == 'b g n d, b d e -> b g n e': return a @ b.unsqueeze(1)
        return block_einsum(equation, a, b)
    def outer(equation, *args):
        if equation == '..., f -> ... f': return args[0].unsqueeze(-1)*args[1]
        return rotary_einsum(equation, *args)
    def positive_pad(x, sizes, mode='constant', value=None):
        if (mode == 'constant' and len(sizes) in (2, 4) and
                not (type(sizes[-1]) is int and sizes[-1] < 0) and
                all(type(v) is int and v == 0 for v in sizes[:-1])):
            axis = x.ndim-len(sizes)//2
            shape = list(x.shape); shape[axis] = sizes[-1]
            tail = torch.full(shape, 0.0 if value is None else float(value), dtype=x.dtype, device=x.device)
            return torch.cat((x, tail), dim=axis)
        if all(type(v) is int for v in sizes) and tuple(sizes) in ((0, 0, 1, -1), (0, 0, 0, 0, 1, -1)):
            axis = x.ndim-len(sizes)//2
            slices = [slice(None)]*x.ndim; slices[axis] = slice(None, -1)
            positive = list(sizes); positive[-1] = 0
            return pad(x[tuple(slices)], positive, mode, value)
        return pad(x, sizes, mode, value)
    torch.nn.functional.pad, rotary.einsum, blocks.einsum = positive_pad, outer, attention
    try:
        yield
    finally:
        torch.nn.functional.pad, rotary.einsum, blocks.einsum = pad, rotary_einsum, block_einsum


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'checkpoint', 'fixtures', 'out'):
        p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--fixtures-sha256', required=True)
    a = p.parse_args()
    a.out.mkdir(mode=0o700, parents=True, exist_ok=False)
    result = {'passed': False, 'installed': False, 'scope': 'Core ML export numerical parity only'}
    started = time.monotonic()
    def source_check():
        revision = subprocess.check_output(['git', '-C', str(a.source), 'rev-parse', 'HEAD'], text=True).strip()
        dirty = subprocess.check_output(['git', '-C', str(a.source), 'status', '--porcelain'], text=True)
        if revision != UPSTREAM_REVISION or dirty or digest(a.checkpoint) != CHECKPOINT_SHA256:
            raise ValueError('exact clean upstream source and checkpoint required')
    try:
        source_check()
        manifest = a.fixtures/'result.json'
        if digest(manifest) != a.fixtures_sha256:
            raise ValueError('fixture manifest changed')
        refs = json.loads(manifest.read_text())
        if not refs['passed'] or refs['source_revision'] != UPSTREAM_REVISION or refs['checkpoint_sha256'] != CHECKPOINT_SHA256:
            raise ValueError('independent upstream reference binding differs')
        expected = {r['samples']: r for r in refs['parity']}
        if set(expected) != {32000, 32776, 72000, 80000, 80003} or len(refs['parity']) != 5:
            raise ValueError('reference coverage differs')
        bindings = {}
        for n, row in expected.items():
            for kind, size in (('input', n), ('reference', 2*n)):
                path = a.fixtures/f'{kind}-{n}.f32'
                if path.stat().st_size != 4*size or digest(path) != row[kind+'_sha256']:
                    raise ValueError('reference waveform binding differs')
                bindings[path] = row[kind+'_sha256']
        import numpy as np
        import torch
        import coremltools as ct
        from types import SimpleNamespace
        sys.path.insert(0, str(a.source/'clearvoice/clearvoice'))
        from models.mossformer2_ss.mossformer2 import MossFormer2_SS_16K
        from models.mossformer2_ss.fsmn import UniDeepFsmn_dilated
        from models.mossformer2_ss.mossformer2_block import OffsetScale
        import models.mossformer2_ss.mossformer2_block as blocks
        import rotary_embedding_torch.rotary_embedding_torch as rotary
        torch.set_num_threads(4)
        owner = MossFormer2_SS_16K(SimpleNamespace(encoder_embedding_dim=512, mossformer_sequence_dim=512,
            num_mossformer_layer=24, encoder_kernel_size=16, num_spks=2)).eval()
        owner.model.load_state_dict(torch.load(a.checkpoint, weights_only=True, map_location='cpu')['model'], strict=True)
        for module in owner.modules():
            if isinstance(module, rotary.RotaryEmbedding): module.cache_if_possible = False
        converted, counts = coreml_copy(owner, UniDeepFsmn_dilated, OffsetScale)
        if counts != dict(convolutions=48, group_norms=2, offset_scales=24, fsmns=24):
            raise ValueError('pinned graph census differs')
        class Export(torch.nn.Module):
            def __init__(self, model): super().__init__(); self.model = model
            def forward(self, pcm):
                net = self.model.model; encoded = net.enc(pcm)
                separated = torch.stack([encoded]*2)*net.mask_net(encoded)
                sources = torch.stack([net.dec(separated[i]) for i in range(2)], dim=1)
                return torch.nn.functional.pad(sources, (0, 8))[:, :, :pcm.shape[1]]
        example = torch.from_numpy(np.fromfile(a.fixtures/'input-72000.f32', dtype='<f4').reshape(1, -1))
        with trace_operations(rotary, blocks), torch.inference_mode():
            traced = torch.jit.trace(Export(converted).eval(), example, check_trace=False)
        del converted, owner
        pipeline = ct.PassPipeline.DEFAULT
        pipeline.remove_passes({'common::fuse_layernorm_or_instancenorm'})
        model = ct.convert(traced, source='pytorch', convert_to='mlprogram', pass_pipeline=pipeline,
            inputs=[ct.TensorType(name='pcm', shape=(1, ct.RangeDim(lower_bound=32000, upper_bound=80003, default=72000)), dtype=np.float32)],
            outputs=[ct.TensorType(name='sources', dtype=np.float32)], compute_precision=ct.precision.FLOAT32,
            minimum_deployment_target=ct.target.macOS15, compute_units=ct.ComputeUnit.CPU_AND_GPU, skip_model_load=True)
        package = a.out/'separator.mlpackage'; model.save(str(package)); del traced, model
        model = ct.models.MLModel(str(package), compute_units=ct.ComputeUnit.CPU_AND_GPU)
        rows = []
        for n in (72000, 32000, 32776, 80000, 80003, 72000):
            pcm = np.fromfile(a.fixtures/f'input-{n}.f32', dtype='<f4').reshape(1, -1)
            ref = np.fromfile(a.fixtures/f'reference-{n}.f32', dtype='<f4').reshape(1, 2, -1)
            actual = model.predict({'pcm': pcm})['sources']
            if actual.shape != ref.shape or not np.isfinite(actual).all(): raise ValueError('output shape or values differ')
            error = float(np.linalg.norm(actual-ref)/np.linalg.norm(ref))
            row = dict(samples=n, relative_l2=error, passed=error <= 2e-4)
            rows.append(row); print(json.dumps(row), flush=True)
        source_check()
        if digest(manifest) != a.fixtures_sha256 or any(digest(path) != sha for path, sha in bindings.items()):
            raise ValueError('qualification inputs changed')
        result.update(passed=all(r['passed'] for r in rows), parity=rows, transformations=counts,
            source_revision=UPSTREAM_REVISION, checkpoint_sha256=CHECKPOINT_SHA256,
            fixtures_sha256=a.fixtures_sha256, script_sha256=digest(__file__),
            torch=torch.__version__, coremltools=ct.__version__, physical_gpu_execution_proven=False,
            files={str(f.relative_to(package)): digest(f) for f in sorted(package.rglob('*')) if f.is_file()})
    except BaseException as error:
        result['failure'] = str(error)
        raise
    finally:
        result['elapsed_seconds'] = time.monotonic()-started
        write(a.out/'result.json', result)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
